FROM ubuntu:18.04

WORKDIR /aviatracker.io

RUN apt-get update && apt-get install -y python3 python3-pip
RUN apt-get update && apt-get install -y libpq-dev
RUN apt-get update && apt-get install -y iputils-ping
RUN apt-get update && apt-get install -y vim
RUN apt-get update && apt-get install -y curl

RUN apt-get update && apt-get install -y curl gnupg
RUN apt-get update && apt-get install -y apt-transport-https
RUN apt-get update && apt-get install -y --fix-missing rabbitmq-server

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

RUN mkdir logs
RUN mkdir beat

COPY aviatracker aviatracker
COPY logging.yaml .
COPY docker/resource/launcher.sh /aviatracker.io/
COPY docker/resource/init_launcher.sh /aviatracker.io/
