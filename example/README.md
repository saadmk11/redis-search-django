# Setup Example Project

#### clone the repository:

```bash
git clone git@github.com:saadmk11/redis-search-django.git
```

#### create virtual environment and activate:

```bash
virtualenv -p python3 venv
source venv/bin/activate
```

#### change directory:

```bash
cd redis-search-django/example
```

#### install requirements:

```bash
pip install -r requirements.txt
```

#### run migrations and runserver:

```bash
python manage.py migrate
python manage.py runserver       # http://127.0.0.1:8000/
```
