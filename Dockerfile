from python:3.8 as provtest-base

# add pytest
RUN pip3 install --no-cache-dir pytest

# create directories within container
RUN mkdir -p /app/src
WORKDIR /app

# install dependencies
COPY ./setup.py .
RUN python3 setup.py develop

# install script
COPY ./src /app

CMD [ "pytest" ]
