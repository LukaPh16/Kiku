"""One-time Firefox setup: installs the Kiku Bridge extension for good, with its port and token.

  venv/bin/python -m browser.setup_firefox              # sign if needed, write the policy (asks for sudo)
  venv/bin/python -m browser.setup_firefox --dry-run    # print the policy, change nothing
  venv/bin/python -m browser.setup_firefox --xpi FILE   # use an already signed .xpi

Release Firefox only installs signed add-ons, so the extension is signed once per version on
addons.mozilla.org (unlisted: not published). API keys: https://addons.mozilla.org/developers/addon/api/key/
saved as secrets/amo_api (line 1 = JWT issuer, line 2 = JWT secret) or env AMO_JWT_ISSUER / AMO_JWT_SECRET.

Then /etc/firefox/policies/policies.json (read by the deb and the snap Firefox) gets:
  ExtensionSettings: force-install the signed .xpi (file:// URL with the version in its name,
                     so a new version is reinstalled on the next Firefox start)
  3rdparty:          port + token, read by the extension via browser.storage.managed
The file is root:<you> 0640 because it holds the token. Other policies in it are kept.
"""
import argparse
import getpass
import grp
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import config

EXT_DIR = Path(__file__).resolve().parent / "extension"
DIST_DIR = Path(__file__).resolve().parent / "dist"
POLICY_FILE = Path("/etc/firefox/policies/policies.json")
AMO_KEYS_FILE = config.SECRETS_DIR / "amo_api"


def _log(msg):
    print(f"[KIKU] setup_firefox: {msg}", flush=True)


def manifest():
    return json.loads((EXT_DIR / "manifest.json").read_text())


def ext_id():
    return manifest()["browser_specific_settings"]["gecko"]["id"]


def xpi_path(version):
    return DIST_DIR / f"kiku-bridge-{version}.xpi"


def amo_keys():
    issuer, secret = os.environ.get("AMO_JWT_ISSUER"), os.environ.get("AMO_JWT_SECRET")
    if issuer and secret:
        return issuer, secret
    try:
        lines = [x.strip() for x in AMO_KEYS_FILE.read_text().splitlines() if x.strip()]
    except FileNotFoundError:
        return None
    return (lines[0], lines[1]) if len(lines) >= 2 else None


def sign(version):
    """Signed .xpi for this manifest version (signs on AMO only if it isn't in dist/ yet)."""
    out = xpi_path(version)
    if out.exists():
        _log(f"using {out.name}")
        return out
    keys = amo_keys()
    if keys is None:
        sys.exit(f"[KIKU] setup_firefox: no AMO API keys. Make them at "
                 f"https://addons.mozilla.org/developers/addon/api/key/ and save them in {AMO_KEYS_FILE} "
                 f"(line 1 = JWT issuer, line 2 = JWT secret), or pass --xpi.")
    _log(f"signing version {version} on addons.mozilla.org (unlisted; takes a minute)")
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["npx", "--yes", "web-ext@8", "sign", "--source-dir", str(EXT_DIR), "--artifacts-dir", tmp,
               "--channel", "unlisted", "--api-key", keys[0], "--api-secret", keys[1]]
        if subprocess.run(cmd).returncode != 0:
            sys.exit("[KIKU] setup_firefox: signing failed (version already signed? bump it in manifest.json)")
        xpis = list(Path(tmp).glob("*.xpi"))
        if not xpis:
            sys.exit("[KIKU] setup_firefox: web-ext made no .xpi")
        DIST_DIR.mkdir(exist_ok=True)
        shutil.move(str(xpis[0]), out)
    _log(f"signed: {out}")
    return out


def build_policy(existing, xpi, eid, port, token):
    """existing policies.json dict + Kiku's entries (pure, tested)."""
    doc = json.loads(json.dumps(existing or {}))
    pol = doc.setdefault("policies", {})
    pol.setdefault("ExtensionSettings", {})[eid] = {
        "installation_mode": "force_installed",
        "install_url": Path(xpi).resolve().as_uri(),
    }
    pol.setdefault("3rdparty", {}).setdefault("Extensions", {})[eid] = {"port": port, "token": token}
    return doc


def read_existing():
    try:
        return json.loads(POLICY_FILE.read_text())
    except FileNotFoundError:
        return {}
    except PermissionError:
        out = subprocess.run(["sudo", "cat", str(POLICY_FILE)], capture_output=True, text=True)
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else {}


def write_policy(doc):
    group = grp.getgrgid(os.getgid()).gr_name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(doc, f, indent=2)
        tmp = f.name
    try:
        _log(f"writing {POLICY_FILE} (sudo)")
        cmd = ["sudo", "install", "-D", "-o", "root", "-g", group, "-m", "640", tmp, str(POLICY_FILE)]
        if subprocess.run(cmd).returncode != 0:
            sys.exit("[KIKU] setup_firefox: couldn't write the policy file")
    finally:
        os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser(description="Install the Kiku Bridge in Firefox for good.")
    ap.add_argument("--xpi", help="an already signed .xpi (skips signing)")
    ap.add_argument("--dry-run", action="store_true", help="print the policy, change nothing")
    a = ap.parse_args()
    version = manifest()["version"]
    if a.xpi:
        xpi = Path(a.xpi).resolve()
        if not xpi.exists():
            sys.exit(f"[KIKU] setup_firefox: {xpi} not found")
    elif a.dry_run:
        xpi = xpi_path(version)
    else:
        xpi = sign(version)
    doc = build_policy(read_existing(), xpi, ext_id(), config.BROWSER_PORT, config.BROWSER_TOKEN)
    if a.dry_run:
        shown = json.loads(json.dumps(doc))
        shown["policies"]["3rdparty"]["Extensions"][ext_id()]["token"] = "<token>"
        print(json.dumps(shown, indent=2))
        return
    write_policy(doc)
    _log(f"done for {getpass.getuser()}. Restart Firefox: Kiku Bridge {version} installs itself, already connected.")
    _log("check: about:policies (Active) and about:addons")


if __name__ == "__main__":
    main()
