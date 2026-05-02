from setuptools import setup

APP     = ["fritz_monitor.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile":       "fritz_monitor.icns",
    "plist": {
        "CFBundleName":             "Fritz LTE Monitor",
        "CFBundleDisplayName":      "Fritz LTE Monitor",
        "CFBundleIdentifier":       "de.fritz.lte-monitor",
        "CFBundleVersion":          "1.0.0",
        "CFBundleShortVersionString": "1.0",
        "LSUIElement":              True,   # Menu-Bar-only, kein Dock-Icon
        "NSAppTransportSecurity":   {"NSAllowsLocalNetworking": True},
    },
    "packages": ["fritzconnection", "rumps"],
}

setup(
    app=APP,
    name="Fritz LTE Monitor",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
