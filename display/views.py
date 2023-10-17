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
from django.forms import ModelForm
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.timezone import now, make_aware, is_aware

from django.conf import settings
from django.views.decorators.cache import cache_page
from django.views.decorators.clickjacking import xframe_options_sameorigin
from google.auth.exceptions import RefreshError

from display.models import ImageSlide, CalendarConnection

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

Event = collections.namedtuple("Event", "start summary allday jugend room")

# Create your views here.

logger = logging.getLogger(__name__)

def index(request):
    return render(request, "display/index.html")


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
                    calendarId="primary" and cal.calendar_id,
                    timeMin=googlenow,
                    maxResults=20,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except RefreshError:
            logger.exception("Refresh woes in slideshow")
            continue

        events = events_result.get("items", [])

        if not events:
            continue


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

    slides = [("banner", x) for x in all_slides]
    if today_events and next_events:
        slides.append(("kalender", ("Die nächsten Veranstaltungen", next_events)))

    if preview_events:
        slides.append(("kalender", ("Ausblick", preview_events)))

    if special_event:
        slides.append(("countdown", special_event))
    try:
        slides.append(("kalender_raum", ("Gottesdienste", scrape_gottesdienste())))
    except Exception as e:
        mail_admins("scrape_gottesdienste() nicht so gut", str(e))
        logger.exception("scrape_gottesdienste() nicht so gut")

    response = render(
        request,
        "display/slideshow.html",
        context=dict(
            slides=slides,
            today_events=today_events,
            next_events=next_events,
            special_event=special_event,
            marker_event=current_event or next_event,
        ),
    )

    response.headers["Refresh"] = "300"

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
