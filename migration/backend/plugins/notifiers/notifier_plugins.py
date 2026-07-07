"""
Notification Provider Plugins
File: migration/backend/plugins/notifiers/notifier_plugins.py

Event-Bus-driven notification providers. Each provider implements
NotifierPlugin and subscribes to Event Bus events independently.

Built-in providers:
    EmailNotifier       → SMTP email via smtplib
    SlackNotifier       → Slack Incoming Webhooks
    TeamsNotifier       → Microsoft Teams Incoming Webhooks
    WebhookNotifier     → Generic HTTP POST webhook
    PagerDutyNotifier   → PagerDuty Events API v2

Events each provider listens to (configurable):
    job.started           → migration started
    job.completed         → migration finished successfully
    job.failed            → migration failed
    validation.failed     → post-migration validation failed
    approval.requested    → migration awaiting approval
    drift.detected        → schema drift paused the migration
    cdc.cutover_ready     → CDC ready for manual cutover

All providers are non-blocking — they run in background threads.
Failures are logged but never propagate to the migration workflow.

Configuration stored in secrets_vault (not plaintext):
    Email:     {"smtp_host": "...", "smtp_port": 587, "from": "...", "to": [...]}
    Slack:     {"webhook_url": "https://hooks.slack.com/..."}
    Teams:     {"webhook_url": "https://outlook.office.com/webhook/..."}
    Webhook:   {"url": "https://...", "method": "POST", "headers": {...}}
    PagerDuty: {"routing_key": "...", "severity": "critical"}
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import json


# ── Event severity mapping ─────────────────────────────────────────────────────

SEVERITY_MAP = {
    "job.failed":         "error",
    "validation.failed":  "error",
    "drift.detected":     "warning",
    "job.completed":      "success",
    "job.started":        "info",
    "approval.requested": "info",
    "cdc.cutover_ready":  "info",
}


# ── Base class ─────────────────────────────────────────────────────────────────

class NotifierPlugin(ABC):
    """
    Base class for all notification provider plugins.
    Providers are instantiated with config and called with event dicts.
    """
    name:         str = "base_notifier"
    display_name: str = "Base Notifier"

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    @abstractmethod
    def send(self, event: Dict[str, Any]) -> bool:
        """
        Send a notification for the given Event Bus event.
        Returns True on success, False on failure.
        Never raises — callers treat this as best-effort.
        """

    def should_notify(self, event_type: str) -> bool:
        """Check if this provider is subscribed to this event type."""
        subscribed = self.config.get("subscribed_events")
        if not subscribed:
            return True   # Default: subscribe to all events
        import fnmatch
        return any(fnmatch.fnmatch(event_type, p) for p in subscribed)

    def format_message(self, event: Dict[str, Any]) -> str:
        """Format a human-readable message from an event dict."""
        event_type  = event.get("event_type", "unknown")
        resource_id = event.get("resource_id", "")
        payload     = event.get("payload", {})
        severity    = SEVERITY_MAP.get(event_type, "info").upper()

        parts = [f"[{severity}] {event_type.upper().replace('.', ' ')}"]
        if resource_id:
            parts.append(f"Job: {resource_id[:8]}...")
        if payload.get("table_name"):
            parts.append(f"Table: {payload['table_name']}")
        if payload.get("error_message") or payload.get("error"):
            err = payload.get("error_message") or payload.get("error")
            parts.append(f"Error: {err[:200]}")
        if payload.get("rows_written") is not None:
            parts.append(f"Rows: {payload['rows_written']:,}")

        return " | ".join(parts)


# ── Email ──────────────────────────────────────────────────────────────────────

class EmailNotifier(NotifierPlugin):
    """
    SMTP email notification.
    config: {
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "smtp_user": "alerts@company.com",
      "smtp_password": "...",
      "from_addr": "alerts@company.com",
      "to_addrs": ["dba@company.com", "ops@company.com"],
      "use_tls": true
    }
    """
    name         = "email_notifier"
    display_name = "Email (SMTP)"

    def send(self, event: Dict[str, Any]) -> bool:
        if not self.should_notify(event.get("event_type", "")):
            return True

        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        try:
            msg = MIMEMultipart()
            msg["From"]    = self.config.get("from_addr", "migrations@platform.local")
            msg["To"]      = ", ".join(self.config.get("to_addrs", []))
            msg["Subject"] = f"Migration Alert: {event.get('event_type', 'unknown')}"

            body = self.format_message(event)
            body += f"\n\nFull event:\n{json.dumps(event, indent=2, default=str)}"
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(
                self.config.get("smtp_host", "localhost"),
                int(self.config.get("smtp_port", 587))
            ) as server:
                if self.config.get("use_tls", True):
                    server.starttls()
                if self.config.get("smtp_user"):
                    server.login(self.config["smtp_user"], self.config.get("smtp_password", ""))
                server.send_message(msg)
            return True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"EmailNotifier failed: {e}")
            return False


# ── Slack ──────────────────────────────────────────────────────────────────────

class SlackNotifier(NotifierPlugin):
    """
    Slack Incoming Webhook notification.
    config: {"webhook_url": "https://hooks.slack.com/services/...", "channel": "#migrations"}
    """
    name         = "slack_notifier"
    display_name = "Slack"

    def send(self, event: Dict[str, Any]) -> bool:
        if not self.should_notify(event.get("event_type", "")):
            return True

        webhook_url = self.config.get("webhook_url")
        if not webhook_url:
            return False

        event_type = event.get("event_type", "unknown")
        severity   = SEVERITY_MAP.get(event_type, "info")

        color_map = {"error": "#FF0000", "warning": "#FFA500",
                     "success": "#36A64F", "info": "#0099FF"}
        color = color_map.get(severity, "#0099FF")

        payload = {
            "text":        f"Migration Platform Alert: *{event_type}*",
            "attachments": [{
                "color":  color,
                "text":   self.format_message(event),
                "footer": f"Job ID: {event.get('resource_id', 'N/A')}",
                "ts":     event.get("published_at", ""),
            }]
        }
        if self.config.get("channel"):
            payload["channel"] = self.config["channel"]

        try:
            import urllib.request
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"SlackNotifier failed: {e}")
            return False


# ── Microsoft Teams ────────────────────────────────────────────────────────────

class TeamsNotifier(NotifierPlugin):
    """
    Microsoft Teams Incoming Webhook.
    config: {"webhook_url": "https://outlook.office.com/webhook/..."}
    """
    name         = "teams_notifier"
    display_name = "Microsoft Teams"

    def send(self, event: Dict[str, Any]) -> bool:
        if not self.should_notify(event.get("event_type", "")):
            return True

        webhook_url = self.config.get("webhook_url")
        if not webhook_url:
            return False

        event_type = event.get("event_type", "unknown")
        severity   = SEVERITY_MAP.get(event_type, "info")
        color_map  = {"error": "FF0000", "warning": "FFA500",
                      "success": "36A64F", "info": "0099FF"}

        payload = {
            "@type":      "MessageCard",
            "@context":   "http://schema.org/extensions",
            "themeColor": color_map.get(severity, "0099FF"),
            "summary":    f"Migration Alert: {event_type}",
            "sections":   [{
                "activityTitle": f"Migration Platform: {event_type.upper().replace('.', ' ')}",
                "activityText":  self.format_message(event),
                "facts": [
                    {"name": "Job ID", "value": event.get("resource_id", "N/A")},
                    {"name": "Severity", "value": severity.upper()},
                ],
            }]
        }

        try:
            import urllib.request
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"TeamsNotifier failed: {e}")
            return False


# ── Generic Webhook ────────────────────────────────────────────────────────────

class WebhookNotifier(NotifierPlugin):
    """
    Generic HTTP POST webhook. Sends the full event JSON.
    config: {"url": "https://...", "method": "POST",
             "headers": {"Authorization": "Bearer token"}}
    """
    name         = "webhook_notifier"
    display_name = "HTTP Webhook"

    def send(self, event: Dict[str, Any]) -> bool:
        if not self.should_notify(event.get("event_type", "")):
            return True

        url = self.config.get("url")
        if not url:
            return False

        try:
            import urllib.request
            headers = {"Content-Type": "application/json"}
            headers.update(self.config.get("headers", {}))
            data = json.dumps(event, default=str).encode()
            req  = urllib.request.Request(url=url, data=data, headers=headers,
                                          method=self.config.get("method", "POST"))
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"WebhookNotifier failed: {e}")
            return False


# ── PagerDuty ──────────────────────────────────────────────────────────────────

class PagerDutyNotifier(NotifierPlugin):
    """
    PagerDuty Events API v2.
    config: {"routing_key": "...", "severity": "critical",
             "trigger_on": ["job.failed", "drift.detected"]}
    """
    name         = "pagerduty_notifier"
    display_name = "PagerDuty"

    PAGERDUTY_URL = "https://events.pagerduty.com/v2/enqueue"

    def send(self, event: Dict[str, Any]) -> bool:
        trigger_on = self.config.get("trigger_on",
                                     ["job.failed", "drift.detected", "validation.failed"])
        event_type = event.get("event_type", "")

        import fnmatch
        if not any(fnmatch.fnmatch(event_type, p) for p in trigger_on):
            return True   # Not a PagerDuty event

        routing_key = self.config.get("routing_key")
        if not routing_key:
            return False

        severity = self.config.get("severity", "critical")
        payload  = {
            "routing_key":  routing_key,
            "event_action": "trigger",
            "payload": {
                "summary":   self.format_message(event),
                "severity":  severity,
                "source":    "migration-platform",
                "component": event.get("source_service", "worker"),
                "custom_details": event.get("payload", {}),
            },
            "dedup_key": f"migration-{event.get('resource_id', 'unknown')}-{event_type}",
        }

        try:
            import urllib.request
            req = urllib.request.Request(
                self.PAGERDUTY_URL,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"PagerDutyNotifier failed: {e}")
            return False


# ── Subscription manager ───────────────────────────────────────────────────────

class NotificationManager:
    """
    Subscribes registered notifiers to the Event Bus.
    Call start() once at service startup.
    """
    _active_notifiers: List[NotifierPlugin] = []

    @classmethod
    def register_notifier(cls, notifier: NotifierPlugin) -> None:
        cls._active_notifiers.append(notifier)

    @classmethod
    def start(cls) -> None:
        """Subscribe all registered notifiers to the Event Bus."""
        if not cls._active_notifiers:
            return
        try:
            from backend.kernel.event_bus.event_bus import EventBus

            def _dispatch(event: Dict[str, Any]) -> None:
                for notifier in cls._active_notifiers:
                    try:
                        notifier.send(event)
                    except Exception:
                        pass

            EventBus.subscribe(["*"], _dispatch, run_in_background=True)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"NotificationManager.start failed: {e}")


# ── Registration ──────────────────────────────────────────────────────────────

def register_all_notifiers():
    """Register all built-in notifiers with the PluginManager."""
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType
        for cls in [EmailNotifier, SlackNotifier, TeamsNotifier,
                    WebhookNotifier, PagerDutyNotifier]:
            PluginManager.register(
                plugin_type=PluginType.NOTIFIER,
                name=cls.name,
                plugin_class=cls,
                display_name=cls.display_name,
                is_builtin=True,
            )
        from backend.shared.config.logging import logger
        logger.info("Notifier plugins registered", count=5)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to register notifiers: {e}")
