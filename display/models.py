from datetime import datetime

from django.db import models
from django.conf import settings
from django.utils import timezone


# Create your models here.


class ImageSlide(models.Model):
    title = models.CharField(max_length=1024)
    image = models.ImageField(upload_to="images/%Y/%m/%d/", blank=False)

    show_start = models.DateTimeField(default=timezone.now)
    show_end = models.DateTimeField(default=datetime(3000, 1, 1, 9, 42))

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)


class CalendarConnection(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    calendar_id = models.CharField(max_length=4096)
    summary = models.CharField(max_length=4096, blank=True, default="")
