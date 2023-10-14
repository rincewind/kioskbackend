import collections
from datetime import timedelta, datetime

from allauth.socialaccount.models import SocialToken
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.forms import ModelForm
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.timezone import now, make_aware, is_aware

from django.conf import settings
from django.views.decorators.cache import cache_page
from google.auth.exceptions import RefreshError

from display.models import ImageSlide, CalendarConnection

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# Create your views here.


def index(request):
    return render(request, "display/index.html")


@cache_page(60 * 15)
def show_presentation(request):
    n = now()
    all_slides = list(ImageSlide.objects.filter(show_start__lt=n, show_end__gt=n))

    if not all_slides:
        pass  # FIXME: do something. misconfigured. show leekspin or something.

    today_events = []  # was geht heute so? (Auch Vergangenes)
    next_event = None  # nächstes Ereignis das in der nächsten Stunde startet.
    current_event = (
        None  # letztes Ereignis, wenn es  in der letzten Stunde startete.
    )
    next_events = []  # die nächsten fünf Events
    preview_events = []  # speziell markierte vorschau events
    special_event = None

    for cal in CalendarConnection.objects.all():
        try:
            token = SocialToken.objects.get(
                account__user=cal.user, account__provider="google"
            )
        except SocialToken.DoesNotExist:
            continue
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
            )  # replace with yours

            utcnow = datetime.utcnow()
            start_of_day = datetime(utcnow.year, utcnow.month, utcnow.day)
            googlenow = start_of_day.isoformat() + "Z"  # 'Z' indicates UTC time

            service = build("calendar", "v3", credentials=credentials)
            events_result = (
                service.events()
                .list(
                    calendarId="primary" and cal.calendar_id,
                    timeMin=googlenow,
                    maxResults=20,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except RefreshError:
            continue

        events = events_result.get("items", [])

        if not events:
            continue

        Event = collections.namedtuple("Event", "start summary allday jugend")

        start_of_day = n.replace(hour=0, minute=0, second=0)
        end_of_day = n.replace(hour=23, minute=59, second=59)

        # Prints the start and name of the next 10 events
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            allday = not "dateTime" in event["start"]
            summary = event.get("summary", "")

            is_preview_event = "⏰" in summary
            is_sepecial_event = "🎉" in summary
            is_jugend = "🚸" in summary

            summary = summary.replace("⏰", "").replace("🎉", "").replace("🚸", "")

            start = datetime.fromisoformat(start)
            if not is_aware(start):
                start = make_aware(start, timezone.utc)

            data = Event(start, summary, allday, is_jugend)

            if start_of_day <= start <= end_of_day:
                today_events.append(data)

            if start - timedelta(hours=1) <= n <= start:  # event start within the hour
                next_event = data

            elif (
                start <= n <= start + timedelta(hours=1)
            ):  # event did start in the last hour
                current_event = data

            if end_of_day <= start <= n + timedelta(days=30):
                if len(next_events) < 6 or next_events[-1].start.day == data.start.day:
                    # inlcude max 6 events but always finish the day
                    next_events.append(data)

            if (
                is_preview_event
                and data not in today_events
                and data not in next_events
            ):
                preview_events.append(data)

            if is_sepecial_event and not special_event:
                special_event = data

    response = render(
        request,
        "display/slideshow.html",
        context=dict(
            slides=all_slides,
            next="14 Uhr: Bananenbrotbacken",
            next_events=next_events,
            today_events=today_events,
            next_event=next_event,
            special_event=special_event,
            current_event=current_event,
            preview_events=preview_events,
        ),
    )

    response.headers["Refresh"] = "300"
    response.header["X-Frame-Options"] = "SAMEORIGIN"

    return response


class BannerForm(ModelForm):
    class Meta:
        model = ImageSlide
        fields = ["image", "show_start", "show_end", "title"]


@staff_member_required
def banner_edit(request, pk):
    banner = get_object_or_404(ImageSlide, pk=pk)
    form = BannerForm(instance=banner)
    if request.method == "POST":
        form = BannerForm(request.POST, request.FILES, instance=banner)
        if form.is_valid():
            form.save()
            return redirect("wartungsklappe")

    return render(request, "display/banneredit.html", context=dict(form=form))


@login_required
def wartungsklappe(request):
    try:
        token = SocialToken.objects.get(
            account__user=request.user, account__provider="google"
        )
    except SocialToken.DoesNotExist:
        token = None
        things = {}

    if token:
        credentials = Credentials(
            token=token.token,
            refresh_token=token.token_secret,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.SOCIALACCOUNT_PROVIDERS["google"]["APP"][
                "client_id"
            ],  # replace with yours
            client_secret=settings.SOCIALACCOUNT_PROVIDERS["google"]["APP"]["secret"],
        )  # replace with yours

        service = build("calendar", "v3", credentials=credentials)
        try:
            things = service.calendarList().list().execute()
        except RefreshError:
            things = dict(items=[])

    # Call the Calendar API
    # now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
    # print('Getting the upcoming 10 events')
    # events_result = service.events().list(calendarId='primary', timeMin=now,
    #                                      maxResults=10, singleEvents=True,
    #                                      orderBy='startTime').execute()
    # events = events_result.get('items', [])

    # if not events:
    #    print('No upcoming events found.')
    #    return

    # Prints the start and name of the next 10 events
    # for event in events:
    #    start = event['start'].get('dateTime', event['start'].get('date'))
    #    print(start, event['summary'])

    connected_calendars = {x.calendar_id: x for x in CalendarConnection.objects.all()}
    new_calendars = {
        x["id"]: x["summary"]
        for x in things.get("items", [])
        if x["id"] not in connected_calendars
    }

    if request.method == "POST":
        if "nuke" in request.POST:
            cache.clear()
            messages.success(request, "Frisch durchgewischt! 🪣")

        elif f := request.FILES.get("file"):
            new_banner = ImageSlide.objects.create(title=f"Neuer Banner  ({now()})")
            new_banner.image.save(f.name, f)
            messages.success(request, "Neuer Banner erstellt.")

        elif "connect" in request.POST:
            calid = request.POST.get("connect_calendar")
            if not calid or calid not in new_calendars:
                messages.error(request, "Das hat nicht geklappt.")
            else:
                CalendarConnection.objects.create(
                    user=request.user, calendar_id=calid, summary=new_calendars[calid]
                )
                messages.success(request, f"Kalender verknüpft: {new_calendars[calid]}")
        elif "disconnect" in request.POST:
            calid = request.POST.get("disconnect_calendar")
            if not calid or calid not in connected_calendars:
                messages.error(request, "Das hat nicht geklappt.")
            else:
                obj = CalendarConnection.objects.get(calendar_id=calid)
                obj.delete()

                messages.success(
                    request,
                    f"Kalender entkoppelt: {connected_calendars[calid].summary}",
                )

        return redirect("wartungsklappe")

    banner = ImageSlide.objects.filter(show_end__gt=now() - timedelta(days=30))

    return render(
        request,
        "display/wartungsklappe.html",
        context=dict(
            new_calendars=new_calendars.items(),
            connected_calendars=connected_calendars.items(),
            banner=banner,
        ),
    )
