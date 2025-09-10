"""
imscc_exporter_ext Django application initialization.
"""

from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)

class ImsccExporterExtConfig(AppConfig):
    """
    Configuration for the imscc_exporter_ext Django application.
    """

    name = 'imscc_exporter_ext'
    label = "imscc_exporter_ext"
    verbose_name = "IMSCC Exporter Extensions"

    def ready(self):
        """
        Application initialization code.
        """
        logger.info("imscc_exporter_ext application is ready.")
