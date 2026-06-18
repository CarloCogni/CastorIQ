#!/bin/bash
# Pass all arguments straight to manage.py inside the web container
docker compose -f docker/docker-compose.prod.yml exec web python manage.py "$@"
