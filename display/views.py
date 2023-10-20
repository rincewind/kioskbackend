import collections
import itertools
import logging
import re
from datetime import timedelta, datetime

import requests
from allauth.socialaccount.models import SocialToken
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import mail_admins
from django.forms import ModelForm, SplitDateTimeWidget, SplitDateTimeField, DateTimeInput, DateTimeField
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.timezone import now, make_aware, is_aware

from django.conf import settings
from django.views.decorators.cache import cache_page
from django.views.decorators.clickjacking import xframe_options_sameorigin
from google.auth.exceptions import RefreshError

from display.models import ImageSlide, CalendarConnection, DisplayConfiguration

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

Event = collections.namedtuple("Event", "start summary allday jugend room")

# Create your views here.

logger = logging.getLogger(__name__)

def index(request):
    return render(request, "display/index.html", context=dict(displays=DisplayConfiguration.objects.all()))


def start_of_day():
    utcnow = datetime.utcnow()
    return make_aware(datetime(utcnow.year, utcnow.month, utcnow.day), timezone.utc)

def scrape_gottesdienste():

    gottesdienste = []

    r = requests.get('https://www.kirche-froemern.de/gruppen-angebote/gottesdienste')

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text)

    for t in soup.find_all("table", class_="contenttable"):
        for row1, row2 in itertools.pairwise(t.find_all("tr")):
            date, time = row1.find_all("td")
            sonntag, beschreibung = row2.find_all("td")

            monate = dict(Jan=1, Feb=2, Mar=3, Apr=4, Mai=5, Jun=6, Jul=7, Aug=8, Sep=9, Okt=10, Nov=11, Dez=12)
            month = None
            for monat, imonat in monate.items():
                if monat in date.get_text():
                    month = imonat

            if month is None:
                continue

            day = int(re.sub(r"[^0-9]", "", date.get_text()))

            if not ":" in time.get_text():
                continue

            hour,minute = time.get_text().split(":")

            hour = int(hour)
            minute = int(minute)

            start = datetime.utcnow().replace(month=month, day=day, hour=hour, minute=minute, second=0,microsecond=0)
            start = make_aware(start)

            if start < start_of_day():
                continue


            gottesdienste.append(Event(start, beschreibung.get_text(), False, False, sonntag.get_text()))

    return gottesdienste[:4]





def massage_kalendereintrag(eintrag):
    """
    try and decipher the kalendereintrag. return "Summary", "Room"

    Should be:

    Room @ Event

    But often it's not.

    i.e.

    @Sonnengruppenraum:Pekip -> "Pekip", "Sonnengruppenraum"

    @Mukiraum: CKU Pilates 18-19Uhr -> "CKU Pilates 18-19Uhr", "Mukiraum"

    1/3 Saal@KU
    DienstgespÃ¤ch HAMA @ GemeindebÃ¼ro
    2/3 Saal@Frauenhilfe
    @1/3 Saal: Franz. Kurs
    @Mukiraum: CKU Pilates 18-19Uhr
    3/3 Saal @ WirbelsÃ¤ulengymnastik
    KU@Saal
    1/3 Saal@Bastelkreis
    2/3 Saal@Seniorenkreis
    JugendrÃ¤ume@FreakyFriday
    MuKiraum @ TagesmÃ¼tter
    1/3 Saal @ Dienstagsfrauen
    1/3 Saal@ Jugend MAGK
    9-12.15 h PEKiP / CKU SonnengrupRraum
    3/3 Saal @ Gymnastik
    2/3 Saal @ Posaunenchor
    MuKiRaum @ PEKIP/ CKU
    Yoga Kurs @ 1/3 Saal
    MuKiRaum@RÃ¼ckbildungsgymnastik
    Saal 1/3@Geburtsvorbereitungskurs
    @Sonnengruppenraum:Pekip
    1/3Saal @ Cafe Knirps
    """
    eintrag = eintrag.strip()

    if len(eintrag) < 5:
        # generate complaint?
        return eintrag

    known_rooms = {"1/3 Saal": "⅓ Saal",
                   "1/3Saal": "⅓ Saal",
                  "2/3 Saal": "⅔ Saal",
                  "3/3 Saal": "Ganzer Saal",
                  "MuKiRaum": "MuKi-Raum",
                  "Sonnengruppenraum": "Sonnengruppenraum",
                  "SonnengrupRraum": "Sonnengruppenraum",
                  "Jugendräume": "Jugendräume",
                  "Saal": "Ganzer Saal",
                  "Gemeindebüro" : "Gemeindebüro",
                  }

    room = ""
    summary = eintrag

    for name, alias in known_rooms.items():
        if name.lower() in eintrag.lower():
            # we found a known room.
            room = alias
            s = re.sub('(?i)' + re.escape(name.lower()), "", eintrag)
            s = s.replace("@", "").strip()
            if s[0] == ":":
                s = s[1:]

            if s[-1] == ":":
                s = s[:-1]

            summary = s
            break

    if not room and "@" in eintrag:
        if eintrag[0] == "@" and ":" in eintrag:
            lhs, rhs = eintrag[1:].split(":")
        else:
            lhs, rhs = eintrag.split("@")

        room = lhs.strip()
        summary = rhs.strip()


    if summary:
        return summary, room
    else: # FIXME: add complaints department?
        return eintrag, ""



import pprint

@staff_member_required
def kalender_dump(request):
    data = ""

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
            )

            if not credentials or not credentials.valid or credentials.expired and token.token_secret:
                credentials.refresh()
                token.token = credentials.token
                if credentials.refresh_token and credentials.refresh_token != token.token_secret:
                    token.token_secret = credentials.refresh_token

                token.save()

            googlenow = start_of_day().isoformat() + "Z"  # 'Z' indicates UTC time

            service = build("calendar", "v3", credentials=credentials)
            events_result = (
                service.events()
                .list(
                    calendarId="primary" and cal.calendar_id,
                    timeMin=googlenow,
                    maxResults=50,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except RefreshError:
            logger.exception("Refresh woes in slideshow")
            continue

        data += "\n\n" + pprint.pformat(events_result.get("items", []))

    return HttpResponse(data, content_type="text/plain")

@xframe_options_sameorigin
@cache_page(60 * 15)
def show_presentation(request, display=""):
    show_controls = not not request.GET.get("kontrolle") # should be UserAgent or something like that.

    try:
        cfg = DisplayConfiguration.objects.get(name=display)
    except DisplayConfiguration.DoesNotExist:
        cfg = DisplayConfiguration.objects.order_by("name").first()

    slides = []
    calendar_events = {} # cache calendar events

    n = now()
    start_of_day = n.replace(hour=0, minute=0, second=0)
    end_of_day = n.replace(hour=23, minute=59, second=59)

    for item in cfg.items.filter(show_start__lt=n, show_end__gt=n).order_by("position"):
        if item.typ == "banner":
            if item.banner.show_start < n and item.banner.show_end > n:
                slides.append(("banner", item.banner))
        elif item.typ == "gottesdienste":
            try:
                slides.append(("kalender_raum", ("Gottesdienste", scrape_gottesdienste())))
            except Exception as e:
                mail_admins("scrape_gottesdienste() nicht so gut", str(e))
                logger.exception("scrape_gottesdienste() nicht so gut")

        elif item.calendar: # kalender item
            if item.calendar.pk in calendar_events:
                today_events, next_event, current_event, next_events, preview_events, special_event = calendar_events[item.calendar.pk]
            else:
                today_events = []  # was geht heute so? (Auch Vergangenes)
                next_event = None  # nächstes Ereignis das in der nächsten Stunde startet.
                current_event = (
                    None  # letztes Ereignis, wenn es  in der letzten Stunde startete.
                )
                next_events = []  # die nächsten fünf Events
                preview_events = []  # speziell markierte vorschau events
                special_event = None
                calcfg = item.calendar
                events = calcfg.load_events()

                for event in events:
                    start = event["start"].get("dateTime", event["start"].get("date"))
                    allday = not "dateTime" in event["start"]
                    summary = event.get("summary", "")


                    is_preview_event = "⏰" in summary
                    is_sepecial_event = "🎉" in summary
                    is_jugend = "🚸" in summary

                    summary = summary.replace("⏰", "").replace("🎉", "").replace("🚸", "")

                    try:
                        summary, room = massage_kalendereintrag(summary)
                    except Exception as e:
                        mail_admins("massage_kalendereintrag ist unglücklich", str(e))
                        logger.exception("massage_kalendereintrag ist unglücklich")
                        room = ""

                    is_jugend = is_jugend or room == "Jugendräume" # temporary special case?!

                    start = datetime.fromisoformat(start)
                    if not is_aware(start):
                        start = make_aware(start, timezone.utc)

                    data = Event(start, summary, allday, is_jugend, room)

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


                calendar_events[item.calendar.pk] = (today_events, next_event, current_event, next_events, preview_events, special_event)


            if today_events and next_events and item.typ=="next_events":
                slides.append(("kalender", ("Die nächsten Veranstaltungen", next_events)))

            if preview_events and item.typ=="preview_events":
                slides.append(("kalender", ("Ausblick", preview_events)))

            if special_event and item.typ=="kalender_countdown":
                slides.append(("countdown", special_event))



    response = render(
        request,
        "display/slideshow.html",
        context=dict(
            slides=slides,
            today_events=today_events,
            next_events=next_events,
            special_event=special_event,
            marker_event=current_event or next_event,
            show_controls=show_controls,
        ),
    )

    response.headers["Refresh"] = "300"

    return response


class DateTimeLocalInput(DateTimeInput):
    input_type = "datetime-local"


class DateTimeLocalField(DateTimeField):
    # Set DATETIME_INPUT_FORMATS here because, if USE_L10N
    # is True, the locale-dictated format will be applied
    # instead of settings.DATETIME_INPUT_FORMATS.
    # See also:
    # https://developer.mozilla.org/en-US/docs/Web/HTML/Date_and_time_formats

    input_formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M"
    ]
    widget = DateTimeLocalInput(format="%Y-%m-%dT%H:%M")



class BannerForm(ModelForm):
    class Meta:
        model = ImageSlide
        fields = ["image", "title", "show_start", "show_end"]

@staff_member_required
def banner_edit(request, pk):
    banner = get_object_or_404(ImageSlide, pk=pk)
    form = BannerForm(instance=banner)
    if request.method == "POST":
        form = BannerForm(request.POST, request.FILES, instance=banner)
        if form.is_valid():
            form.save()
            for cfg in DisplayConfiguration.objects.all():
                for item in cfg.items.all():
                    if item.typ == "banner" and item.banner.pk == banner.pk:
                        break
                else:
                    cfg.items.create(typ="banner", banner=banner)

            return redirect("wartungsklappe")

    return render(request, "display/banneredit.html", context=dict(form=form, banner=banner))


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
        except RefreshError as e:
            logger.exception("Refresh woes in wartungsklappe")
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
            new_banner = ImageSlide.objects.create(title=f"Neuer Banner  ({now().strftime('%d.%m.%y %H:%M:%S')})")
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
