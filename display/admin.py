from django.contrib import admin

from display.models import ImageSlide


# Register your models here.


@admin.register(ImageSlide)
class ImageSlideAdmin(admin.ModelAdmin):
    list_display = ["title", "image"]
