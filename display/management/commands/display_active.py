import sys
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils.timezone import now

from display.models import DisplayConfiguration
from display.views import load_events, iter_items


class Command(BaseCommand):
    help = "Check, wether the displat should be active (TV on)"

    def add_arguments(self, parser):
        parser.add_argument("display_name", type=str)

    def handle(self, *args, **options):

        cfg = DisplayConfiguration.objects.get(name=options["display_name"])

        for item in iter_items(cfg):
            if item.calendar:
                today_events = load_events(item.calendar)[0]
                n = now()
                active = False
                for event in today_events:
                    active |= n - timedelta(hours=1) <= event.start <= n + timedelta(hours=1)
                    active |= event.start <= n <= event.end
                    active |= n  <= event.end <= n + timedelta(hours=1)
                    if active:
                        break

                if active:
                    break



        if active:
            self.stdout.write(
                self.style.SUCCESS("Display should be ON")
            )

        else:
            self.stdout.write(
                self.style.SUCCESS("Display should be OFF")
            )
            sys.exit(5)
