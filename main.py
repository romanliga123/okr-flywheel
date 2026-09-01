# OKR Agent - GUI Entry Point
import sys
import os

# Fix SSL certificates for PyInstaller bundle
try:
    import certifi
    os.environ['SSL_CERT_FILE'] = certifi.where()
    os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
except ImportError:
    pass

from gui import main

if __name__ == "__main__":
    main()
