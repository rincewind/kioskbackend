FROM python:3.11

RUN apt-get clean

RUN apt-get update && \
    apt-get install -y && \
    pip3 install uwsgi && \
    pip3 install pipenv
    
RUN apt-get install locales
RUN sed -i 's/# de_DE.UTF-8 UTF-8/de_DE.UTF-8 UTF-8/g' /etc/locale.gen    
RUN locale-gen
ENV LANG="de_DE.UTF-8" LC_ALL="de_DE.UTF-8"


RUN mkdir -p /data
RUN mkdir -p  /media
RUN mkdir -p  /static


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

