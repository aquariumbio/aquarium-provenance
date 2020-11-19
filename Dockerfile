FROM python:3.9 AS provtest-base

# add pytest
RUN pip3 install --no-cache-dir pytest==6.1.2

# create directories within container
RUN mkdir -p /app/src
WORKDIR /app

# install dependencies
COPY ./setup.py .
RUN python3 setup.py develop

# install script
COPY ./src /app

CMD [ "pytest" ]
