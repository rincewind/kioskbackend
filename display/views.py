import collections
import itertools
import locale
import logging
import re
from datetime import timedelta, datetime
import time
import requests
from allauth.socialaccount.models import SocialToken
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import mail_admins
from django.forms import ModelForm, SplitDateTimeWidget, SplitDateTimeField, DateTimeInput, DateTimeField, \
    ModelMultipleChoiceField, CheckboxSelectMultiple
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

Event = collections.namedtuple("Event", "start end summary allday jugend room")

# Create your views here.

logger = logging.getLogger(__name__)

def index(request):
    return render(request, "display/index.html", context=dict(displays=DisplayConfiguration.objects.all()))


def start_of_day():
    utcnow = datetime.utcnow()
    return make_aware(datetime(utcnow.year, utcnow.month, utcnow.day), timezone.utc)

def scrape_gottesdienste():
    locale.setlocale(locale.LC_ALL, 'de_DE.UTF-8')

    gottesdienste = []

    r = requests.get('https://www.kirche-froemern.de/gruppen-angebote/gottesdienste')

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text)

    for t in soup.find_all("table", class_="contenttable"):

        first_row = t.find("tr")
        if len(first_row.find_all("td")) == 4: # multi table
            orte = []
            for row in t.find_all("tr"):
                if len(row.find_all("td")) < 4:
                    continue

                if not orte:
                    orte.append("")
                    for col in row.find_all("td"):
                        t = col.get_text().strip()
                        if t:
                            orte.append(t.strip())

                    continue

                day = None
                untertitel = ""
                for ort, col in zip(orte, row.find_all("td")):

                    try:
                        if not ort:
                            p_elements = col.find_all("p")
                            datum = p_elements[0].get_text().strip()
                            untertitel = " ".join(e.get_text().strip() for e in p_elements[1:])
                            day = time.strptime(datum, "%d. %B")

                        else:
                            p_elements = col.find_all("p")
                            uhrzeit = p_elements[0].get_text().strip()
                            titel = " ".join(e.get_text().strip() for e in p_elements[1:])
                            uhrzeit = time.strptime(uhrzeit, "%H:%M")
                            date = datetime.utcnow().replace(day=day.tm_mday, month=day.tm_mon, hour=uhrzeit.tm_hour, minute=uhrzeit.tm_min, second=0)
                            make_aware(date)
                            gottesdienste.append(Event(date, date, titel, False, False, f"{ort} ({untertitel})"))



                    except ValueError:
                        continue # skip if broken


        else:

            for row1, row2 in itertools.pairwise(t.find_all("tr")):
                date, thetime = row1.find_all("td")
                sonntag, beschreibung = row2.find_all("td")

                monate = dict(Jan=1, Feb=2, Mar=3, Apr=4, Mai=5, Jun=6, Jul=7, Aug=8, Sep=9, Okt=10, Nov=11, Dez=12)
                month = None
                for monat, imonat in monate.items():
                    if monat in date.get_text():
                        month = imonat

                if month is None:
                    continue

                day = int(re.sub(r"[^0-9]", "", date.get_text()))

                if not ":" in thetime.get_text():
                    continue

                hour,minute = thetime.get_text().split(":")

                hour = int(hour)
                minute = int(minute)

                start = datetime.utcnow().replace(month=month, day=day, hour=hour, minute=minute, second=0,microsecond=0)
                start = make_aware(start)

                if start < start_of_day():
                    continue


                gottesdienste.append(Event(start, start, beschreibung.get_text(), False, False, sonntag.get_text()))


    return gottesdienste[:8]





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


def load_events(calcfg):
    n = now()
    start_of_day = n.replace(hour=0, minute=0, second=0)
    end_of_day = n.replace(hour=23, minute=59, second=59)

    today_events = []  # was geht heute so? (Auch Vergangenes)
    next_event = None  # nächstes Ereignis das in der nächsten Stunde startet.
    current_event = (
        None  # letztes Ereignis, wenn es  in der letzten Stunde startete.
    )
    next_events = []  # die nächsten fünf Events
    preview_events = []  # speziell markierte vorschau events
    special_event = None
    events = calcfg.load_events()

    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))

        allday = not "dateTime" in event["start"]
        summary = event.get("summary", "")

        is_preview_event = "⏰" in summary
        is_special_event = "🎉" in summary
        is_jugend = "🚸" in summary
        is_hidden = "👻" in summary

        is_preview_event |= "[pre]" in summary
        is_special_event |= "[codo]" in summary
        is_jugend |= "[evj]" in summary
        is_hidden |= "[intern]" in summary

        summary = summary.replace("⏰", "").replace("🎉", "").replace("🚸", "")
        summary = summary.replace("[pre]", "").replace("[codo]", "").replace("[evj]", "").replace("[intern]", "")

        if is_hidden:
            continue


        try:
            summary, room = massage_kalendereintrag(summary)
        except Exception as e:
            mail_admins("massage_kalendereintrag ist unglücklich", str(e))
            logger.exception("massage_kalendereintrag ist unglücklich")
            room = ""

        is_jugend = is_jugend or room == "Jugendräume"  # temporary special case?!

        start = datetime.fromisoformat(start)
        end = datetime.fromisoformat(end)

        if not is_aware(start):
            start = make_aware(start, timezone.utc)

        if not is_aware(end):
            end = make_aware(end, timezone.utc)

        data = Event(start, end, summary, allday, is_jugend, room)

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

        if is_special_event and not special_event:
            special_event = data

    return (today_events, next_event, current_event, next_events, preview_events, special_event)


def iter_items(cfg):
    n = now()
    return cfg.items.filter(show_start__lt=n, show_end__gt=n).order_by("position")

@xframe_options_sameorigin
@cache_page(60 * 15)
def show_presentation(request, display="", portrait=""):
    show_controls = not not request.GET.get("kontrolle") # should be UserAgent or something like that.
    portrait = not not portrait
    try:
        cfg = DisplayConfiguration.objects.get(name=display)
    except DisplayConfiguration.DoesNotExist:
        cfg = DisplayConfiguration.objects.order_by("name").first()

    if cfg.effect:
        response = render(
            request,
            f"display/{cfg.effect}.html",
            context=dict(portrait=portrait))

        response.headers["Refresh"] = "300"
        return response


    slides = []
    now_slide = None
    calendar_events = {} # cache calendar events
    today_events = []
    next_event = None
    current_event = None
    next_events = []
    preview_events = []
    special_event=None

    n = now()

    for item in iter_items(cfg):
        if item.typ == "banner":
            if item.now_start is not None and item.now_start < n and item.now_start + item.how_long > n:
                now_slide = item.banner

            elif item.banner.show_start < n and item.banner.show_end > n:
                slides.append(("banner", item.banner))

        elif item.typ == "gottesdienste":
            try:
                slides.append(("kalender_raum", ("Gottesdienste", scrape_gottesdienste())))
            except Exception as e:
                if settings.DEBUG:
                    raise
                mail_admins("scrape_gottesdienste() nicht so gut", str(e))
                logger.exception("scrape_gottesdienste() nicht so gut")

        elif item.calendar: # kalender item
            if item.calendar.pk in calendar_events:
                today_events, next_event, current_event, next_events, preview_events, special_event = calendar_events[item.calendar.pk]
            else:
                today_events, next_event, current_event, next_events, preview_events, special_event = load_events(item.calendar)
                calendar_events[item.calendar.pk] = (today_events, next_event, current_event, next_events, preview_events, special_event)


            if (today_events or now_slide) and next_events and item.typ=="next_events":
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
            now_slide=now_slide,
            portrait=portrait,
            two_column=cfg.two_column,
            show_clock=cfg.show_clock,
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


    displays = ModelMultipleChoiceField(required=False, widget=CheckboxSelectMultiple(), queryset=DisplayConfiguration.objects.all())


@staff_member_required
def banner_edit(request, pk):
    banner = get_object_or_404(ImageSlide, pk=pk)
    form = BannerForm(instance=banner)
    if request.method == "POST":
        form = BannerForm(request.POST, request.FILES, instance=banner)
        if form.is_valid():
            form.save()
            displays = form.cleaned_data["displays"]
            # FIXME: do something with displays here

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

            for cfg in DisplayConfiguration.objects.all():
                for item in cfg.items.all():
                    if item.typ == "banner" and item.banner.pk == new_banner.pk:
                        break
                else:
                    cfg.items.create(typ="banner", banner=new_banner)

            messages.success(request, "Neuer Banner erstellt und auf allen Displays aktiviert.")

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


def should_be_always_on(n):
    """
    this needs a way to configure always on time (like crontab entries, for stull like every Wednesday vom 9-5
    FIXME: this is a temporary solution with the gemeindebüro zeiten
    """
    if n.isoweekday() in (2,4): # tuesdays and thursdaus
        if 8 <= n.hour <= 12: # 10-12 CET in UTC plus one hour in each direction
            return True

    if n.isoweekday() == 3: # wednesdays
        if 13 <= n.hour <= 17: # 15-17 CET in UTC plus one hour in each direction
            return True

    return False


def display_status(request, display):
    cfg = get_object_or_404(DisplayConfiguration, name=display)
    preview = request.GET.get("vorschau")
    data = {}
    ns = [now().replace(minute=0, second=30)]

    if preview:
        ns = [now().replace(hour=2, minute=0, second=30)]
        for step in range(48):
            ns.append(ns[-1] + timedelta(minutes=30))
            if ns[-1].day != ns[0].day:
                ns.pop()
                break


    for item in iter_items(cfg):
        if item.typ == "banner" and item.now_start:
            for n in ns:
                active = data.get(n, False)
                if active:
                    continue

                active |= item.now_start - timedelta(hours=1) <= n <= item.now_start + item.how_long + timedelta(hours=1)

                data[n] = active

                if active and not preview:
                    break


        elif item.calendar:
            today_events = load_events(item.calendar)[0]
            if not today_events:
                break

            for n in ns:
                active = data.get(n, False)
                if active:
                    continue

                active |= should_be_always_on(n)

                for event in today_events:
                    active |= n - timedelta(hours=1) <= event.start <= n + timedelta(hours=1)
                    active |= event.start <= n <= event.end + timedelta(hours=1)
                    if active:
                        break

                data[n] = active

                if active and not preview:
                    break

    if len(data) > 1 or preview:
        return render(
            request,
            "display/onoff.html",
            context=dict(rows=data.items()))

    if data and list(data.values())[0]:
        return HttpResponse("ON", status=200)

    else:
        return HttpResponse("OFF", status=204)
