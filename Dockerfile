FROM nginx:stable

# Install python
RUN apt-get update && apt-get -y install python-dev build-essential python-pip

WORKDIR /app

COPY . /app
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY configs/authenticator.cfg /etc/scitokens-auth/authenticator.cfg
COPY configs/nginx.conf /etc/nginx/nginx.conf

EXPOSE 80 443

CMD ["/bin/bash", "/app/tools/run_app.sh"]
