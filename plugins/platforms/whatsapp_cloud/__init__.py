"""WhatsApp Cloud API gateway adapter — official Meta Graph API (HP-403).

Complementary to the existing web-bridge WhatsApp adapter
(``plugins/platforms/whatsapp/``).  This adapter uses the official
Meta WhatsApp Cloud API via ``graph.facebook.com``, which does not
require a phone running a web browser.
"""


def register_plugin(manager):
    """Register the WhatsApp Cloud API adapter with the plugin manager."""
    from plugins.platforms.whatsapp_cloud.adapter import WhatsAppCloudAdapter
    manager.register_adapter("whatsapp_cloud", WhatsAppCloudAdapter)
