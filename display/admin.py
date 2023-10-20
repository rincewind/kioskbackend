from django.contrib import admin

from display.models import ImageSlide, DisplayConfiguration, DisplayConfigurationItem


@admin.register(ImageSlide)
class ImageSlideAdmin(admin.ModelAdmin):
    list_display = ["title", "image"]

#@admin.register(DisplayImageSlide)
class DisplayConfigurationItemInline(admin.StackedInline):
    model = DisplayConfigurationItem


@admin.register(DisplayConfiguration)
class DisplayConfigurationAdmin(admin.ModelAdmin):
    list_display = ['name', 'title']
    inlines = [DisplayConfigurationItemInline]
