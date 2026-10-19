"""
Searcharr
Sonarr & Radarr Telegram Bot
Main Bot Class
By Todd Roberts
https://github.com/toddrob99/searcharr
"""
import os
import sys
import threading
import time

from telegram.ext import ApplicationBuilder, CallbackQueryHandler

from api.sonarr import Sonarr
from api.radarr import Radarr
from bot.utils.log import set_up_logger
from bot.utils.database import init_db
from bot.commands import register_commands
from bot.callbacks import main_callback_handler
import settings
from config.language import load_language


class SearcharrBot:
    """Main Searcharr bot class that initializes and runs the Telegram bot."""

    SERVICE_RETRY_FIRST_DELAY = 5
    SERVICE_RETRY_MAX_DELAY = 60
    SERVICE_RETRY_TOTAL = 600  # seconds of retrying before exiting
    _sleep = staticmethod(time.sleep)
    
    def __init__(self, token, dev_mode=False, verbose=False):
        """Initialize the Searcharr bot.
        
        Args:
            token (str): Telegram bot token
            dev_mode (bool, optional): Enable developer mode. Defaults to False.
            verbose (bool, optional): Enable verbose logging. Defaults to False.
        """
        self.dev_mode = dev_mode
        self.token = token
        self.logger = set_up_logger("bot")
        self.logger.info("Initializing SearcharrBot...")
        
        # Initialize database
        init_db()
        
        # Load language
        self.lang = load_language()
        
        # Initialize service clients
        self.sonarr = self._init_service("sonarr", verbose)
        self.radarr = self._init_service("radarr", verbose)
        
        # Check and validate settings
        self._validate_settings()
        
    def _init_service(self, service_name, verbose):
        """Initialize a service client with error handling.
        
        Args:
            service_name (str): Name of the service to initialize (sonarr, radarr)
            verbose (bool): Enable verbose logging
            
        Returns:
            object: Service client or None if not enabled/failed
        """
        # Check if service is enabled
        enabled_setting = f"{service_name}_enabled"
        if not hasattr(settings, enabled_setting):
            # Setting doesn't exist, create it and set to False
            setattr(settings, enabled_setting, False)
            self.logger.warning(
                f"No {enabled_setting} setting found. If you want Searcharr to support {service_name.title()}, "
                f"please refer to the sample settings on github and add settings for {service_name.title()} to settings.py."
            )
            return None
        elif not getattr(settings, enabled_setting):
            # Setting exists but is False
            self.logger.info(f"{service_name.title()} is disabled in settings")
            return None
        
        if service_name not in ("sonarr", "radarr"):
            self.logger.error(f"Unknown service: {service_name}")
            return None

        # Sonarr/Radarr are often still starting when the host boots. Disabling
        # a service on the first failure left the bot running without it until
        # someone restarted it, so retry, then exit and let Docker restart us.
        waited = 0
        delay = self.SERVICE_RETRY_FIRST_DELAY
        while True:
            try:
                return self._connect_service(service_name, verbose)
            except Exception as e:
                if waited >= self.SERVICE_RETRY_TOTAL:
                    self.logger.critical(
                        f"{service_name.title()} still unreachable after {waited}s ({type(e).__name__}); exiting so the container restarts"
                    )
                    sys.exit(75)
                self.logger.error(
                    f"Failed to initialize {service_name.title()} ({type(e).__name__}); retrying in {delay}s"
                )
                self._sleep(delay)
                waited += delay
                delay = min(delay * 2, self.SERVICE_RETRY_MAX_DELAY)

    def _connect_service(self, service_name, verbose):
        if service_name == "sonarr":
            from bot.services.sonarr_service import configure_sonarr
            return configure_sonarr(Sonarr(settings.sonarr_url, settings.sonarr_api_key, verbose))
        from bot.services.radarr_service import configure_radarr
        return configure_radarr(Radarr(settings.radarr_url, settings.radarr_api_key, verbose))
    
    def _validate_settings(self):
        """Validate and set defaults for settings."""
        from config.validator import validate_settings
        validate_settings()
    
    async def _handle_error(self, update, context):
        """Generic error handler for the bot.
        
        Args:
            update (Update): The update that caused the error
            context (CallbackContext): The context that raised the error
        """
        self.logger.error(f"Error occurred: {context.error}")
        try:
            await update.callback_query.answer()
        except Exception:
            pass
    
    def _start_liveness_watchdog(self, application):
        """Exit the process if the polling loop dies so the container restart
        policy can recover us.

        Failure history this guards against: the getUpdates loop has twice
        stopped while the process stayed alive (2026-06-19 boot-time DNS
        failure, 2026-07-13 Bad Gateway storm) — a zombie state that
        `restart: always` can never fix because the process never exits.
        Fail-fast + Docker restart is the recovery path.
        """
        def watch():
            time.sleep(180)  # grace period for startup
            misses = 0
            while True:
                time.sleep(60)
                alive = False
                try:
                    alive = bool(
                        application.running
                        and application.updater is not None
                        and application.updater.running
                    )
                except Exception:
                    alive = False
                if alive:
                    misses = 0
                else:
                    misses += 1
                    self.logger.warning(
                        f"Liveness check failed ({misses}/3): polling loop not running"
                    )
                    if misses >= 3:
                        self.logger.critical(
                            "Polling loop is dead — exiting so the container restart policy can recover."
                        )
                        os._exit(75)  # EX_TEMPFAIL

        t = threading.Thread(target=watch, daemon=True, name="liveness-watchdog")
        t.start()

    def run(self):
        """Initialize and run the Telegram bot."""
        self.logger.info("Starting Searcharr bot...")

        # Build the application. Explicit getUpdates timeouts prevent the
        # long-poll from hanging forever on a silently-dropped connection
        # (the 2026-07-13 zombie: Bad Gateway storm, then a stalled poll).
        application = (
            ApplicationBuilder()
            .token(self.token)
            .job_queue(None)
            .get_updates_connect_timeout(10)
            .get_updates_read_timeout(30)
            .get_updates_write_timeout(10)
            .get_updates_pool_timeout(10)
            .build()
        )

        # Register command handlers from each module
        register_commands(application, self)

        # Register the main callback handler
        application.add_handler(CallbackQueryHandler(main_callback_handler(self)))

        # Add error handler if not in dev mode
        if not self.dev_mode:
            application.add_error_handler(self._handle_error)
        else:
            self.logger.info("Developer mode is enabled; skipping registration of error handler--exceptions will be raised.")

        # Watchdog: if polling dies without killing the process, exit so
        # Docker restarts us instead of leaving a zombie.
        self._start_liveness_watchdog(application)

        # Start the bot. bootstrap_retries=-1 retries network failures during
        # startup indefinitely (the 2026-06-19 crash: DNS not ready at boot
        # killed the bot instead of retrying).
        try:
            application.run_polling(bootstrap_retries=-1)
        except Exception as e:
            self.logger.critical(f"Bot terminated with unhandled exception: {e!r}")
            sys.exit(1)