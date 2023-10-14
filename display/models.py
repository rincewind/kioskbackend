from datetime import datetime

from django.db import models
from django.conf import settings
from django.utils import timezone


# Create your models here.


class ImageSlide(models.Model):
    title = models.CharField("Titel", max_length=1024)
    image = models.ImageField("Bild", upload_to="images/%Y/%m/%d/", blank=False)

    show_start = models.DateTimeField("Erste Anzeige", default=timezone.now, help_text="Ab wann soll dieser Banner angezeigt werden? (tt.mm.jjjj hh:mm)")
    show_end = models.DateTimeField("Letzte Anzeige", default=datetime(3000, 1, 1, 9, 42), help_text="Ab wann soll dieser Banner nicht mehr angezeigt werden? (tt.mm.jjjj hh:mm)")

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)


class CalendarConnection(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    calendar_id = models.CharField(max_length=4096)
    summary = models.CharField(max_length=4096, blank=True, default="")
