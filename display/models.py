import logging
from datetime import datetime, timedelta

from allauth.socialaccount.models import SocialToken
from django.db import models
from django.conf import settings
from django.utils import timezone
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


# Create your models here.
logger = logging.getLogger(__name__)

class ImageSlide(models.Model):
    title = models.CharField("Titel", max_length=1024)
    image = models.ImageField("Bild", upload_to="images/%Y/%m/%d/", blank=False)

    show_start = models.DateTimeField("Erste Anzeige", default=timezone.now, help_text="Ab wann soll dieser Banner angezeigt werden? (tt.mm.jjjj hh:mm)")
    show_end = models.DateTimeField("Letzte Anzeige", default=datetime(3000, 1, 1, 9, 42), help_text="Ab wann soll dieser Banner nicht mehr angezeigt werden? (tt.mm.jjjj hh:mm)")

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Banner {self.title}"


class CalendarConnection(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    calendar_id = models.CharField(max_length=4096)
    summary = models.CharField(max_length=4096, blank=True, default="")

    def __str__(self):
        return f"Calendar {self.summary}"

    def load_events(self, howmany=30):

        try:
            token = SocialToken.objects.get(
                account__user=self.user, account__provider="google"
            )
        except SocialToken.DoesNotExist:
            return []

        try:
            credentials = Credentials(
                token=token.token,
                refresh_token=token.token_secret,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.SOCIALACCOUNT_PROVIDERS["google"]["APP"][
                    "client_id"
                ],  # replace with yours
                client_secret=settings.SOCIALACCOUNT_PROVIDERS["google"]["APP"][
                    "secret"
                ],
            )

            if not credentials or not credentials.valid or credentials.expired and token.token_secret:
                credentials.refresh()
                token.token = credentials.token
                if credentials.refresh_token and credentials.refresh_token != token.token_secret:
                    token.token_secret = credentials.refresh_token

                token.save()

            utcnow = datetime.utcnow()
            start_of_day = datetime(utcnow.year, utcnow.month, utcnow.day)
            googlenow = start_of_day.isoformat() + "Z"  # 'Z' indicates UTC time

            service = build("calendar", "v3", credentials=credentials)
            events_result = (
                service.events()
                .list(
                    calendarId="primary" and self.calendar_id,
                    timeMin=googlenow,
                    maxResults=howmany,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except RefreshError:
            logger.exception("Refresh woes in slideshow")
            return []

        return events_result.get("items", [])



class DisplayConfiguration(models.Model):
    name = models.CharField(max_length=255, blank=False, unique=True)
    title = models.CharField(max_length=1024, blank=True, default="")

    def __str__(self):
        return f"Display {self.name} ({self.title})"


class DisplayConfigurationItem(models.Model):
    ITEMTYPES = [("banner", "Banner"), ("gottesdienste", "Gottesdienstplan"), ("kalender_countdown", "Countdown aus Kalender"), ("preview_events", "Ausblick"), ("next_events", "Nächste Ereignisse")]

    position = models.IntegerField(default=0)
    show_start = models.DateTimeField("Erste Anzeige", default=timezone.now, help_text="Ab wann soll dieser Banner angezeigt werden? (tt.mm.jjjj hh:mm)")
    show_end = models.DateTimeField("Letzte Anzeige", default=datetime(3000, 1, 1, 9, 42), help_text="Ab wann soll dieser Banner nicht mehr angezeigt werden? (tt.mm.jjjj hh:mm)")

    now_start = models.DateTimeField("Als 'Jetzt' anzeigen", default=None, blank=True, null=True)
    how_long = models.DurationField("Wie lange als 'Jetzt' anzeigen?", default=timedelta(hours=2), blank=False, null=False)

    display = models.ForeignKey(DisplayConfiguration, on_delete=models.CASCADE, related_name="items")


    typ = models.CharField("Sorte", max_length=255, choices=ITEMTYPES)
    banner = models.ForeignKey(ImageSlide, on_delete=models.CASCADE, null=True, blank=True)
    calendar = models.ForeignKey(CalendarConnection, on_delete=models.CASCADE, null=True, blank=True)


    def __str__(self):
        s = f"[{self.position}:{self.display}] {self.get_typ_display()}"

        if self.banner:
            s += f" {self.banner}"

        if self.calendar:
            s += f" {self.calendar}"

        return s




