FROM python:3.11

RUN apt-get clean

RUN apt-get update && \
    apt-get install -y && \
    pip3 install uwsgi && \
    pip3 install pipenv


RUN mkdir /data
RUN mkdir /media
RUN mkdir /static


RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

COPY ./Pipfile /usr/src/app/    
COPY ./Pipfile.lock /usr/src/app/

ENV PIPENV_VENV_IN_PROJECT=1

RUN pipenv --rm || true
RUN pipenv --python 3.11 install --clear --deploy 

COPY ./ /usr/src/app

EXPOSE 8080

CMD ["sh", "run.sh"]

