"""
diagnose_gdac.py - Test GDAC HTTPS access with certifi SSL fix.

Run after `pip install certifi`:
    python diagnose_gdac.py

ASCII output for Windows console.
"""
import urllib.request
import urllib.error
import socket
import ssl
import sys
from time import time

WMO = "5906551"
DAC = "aoml"

MIRRORS = [
    ("IFREMER",  "https://data-argo.ifremer.fr/dac"),
    ("US GODAE", "https://usgodae.org/ftp/outgoing/argo/dac"),
]

UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; argo-monitor-diag/1.0)"
}

# Try to use certifi's CA bundle (fixes the Windows+Anaconda SSL issue).
try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
    print("Using certifi CA bundle: " + certifi.where())
except ImportError:
    CTX = ssl.create_default_context()
    print("WARNING: certifi not installed; using default SSL context.")
    print("Install with: pip install certifi")


def try_request(method, url, timeout=10):
    print()
    print("  " + method + " " + url)
    req = urllib.request.Request(url, method=method, headers=UA_HEADERS)
    t0 = time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            dt = time() - t0
            print("    -> SUCCESS  status=%d  in %.2fs" % (resp.status, dt))
            cl = resp.headers.get("Content-Length")
            ct = resp.headers.get("Content-Type")
            print("    -> Content-Type=%s, Content-Length=%s" % (ct, cl))
            return True
    except urllib.error.HTTPError as e:
        print("    -> HTTPError %d: %s" % (e.code, e.reason))
    except urllib.error.URLError as e:
        print("    -> URLError: %s" % e.reason)
    except socket.timeout:
        print("    -> TIMEOUT after %ds" % timeout)
    except Exception as e:
        print("    -> %s: %s" % (type(e).__name__, e))
    return False


def main():
    print("Python: " + sys.version.split()[0])
    print("OS: " + sys.platform)
    print("Probing float " + WMO + " (DAC=" + DAC + ")")
    print("=" * 70)

    for label, root in MIRRORS:
        print()
        print("--- " + label + "  " + root)
        meta_url = root + "/" + DAC + "/" + WMO + "/" + WMO + "_meta.nc"
        try_request("HEAD", meta_url)
        try_request("GET", meta_url)

    print()
    print("=" * 70)
    print("Done. Send this output to Claude.")


if __name__ == "__main__":
    main()
