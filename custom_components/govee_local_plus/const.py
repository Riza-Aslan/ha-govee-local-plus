"""Constants for Govee Local Plus."""

from datetime import timedelta

DOMAIN = "govee_local_plus"
MANUFACTURER = "Govee"

CONF_MULTICAST_ADDRESS_DEFAULT = "239.255.255.250"
CONF_TARGET_PORT_DEFAULT = 4001
CONF_LISTENING_PORT_DEFAULT = 4002
CONF_COMMAND_PORT = 4003
CONF_DISCOVERY_INTERVAL_DEFAULT = 60

CONF_CUSTOM_EFFECTS = "custom_effects"

SCAN_INTERVAL = timedelta(seconds=30)
# A device is considered unavailable if we have not heard a status response
# from it for three consecutive poll cycles.
DEVICE_TIMEOUT = SCAN_INTERVAL * 3
DISCOVERY_TIMEOUT = 5
# Addresses that failed to bind are retried on this cadence.
REBIND_INTERVAL = timedelta(minutes=5)

# Public Govee scene library (no account, no key): one request per SKU,
# cached on disk afterwards.
SCENE_LIBRARY_URL = "https://app2.govee.com/appsku/v1/light-effect-libraries"
SCENE_LIBRARY_APP_VERSION = "5.6.01"
SCENE_LIBRARY_USER_AGENT = (
    "GoveeHome/5.6.01 (com.ihoment.GoVeeSensor; build:2; iOS 16.5.0) Alamofire/5.6.1"
)
CATALOG_MAX_AGE = timedelta(days=30)
CATALOG_STORE_VERSION = 1

NONE_EFFECT = "none"
