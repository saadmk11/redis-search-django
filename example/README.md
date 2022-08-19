# Setup Example Project

#### Clone the repository:

```bash
git clone git@github.com:saadmk11/redis-search-django.git
```

#### Create a virtual environment and activate it:

```bash
virtualenv -p python3 venv
source venv/bin/activate
```

#### Change to project directory:

```bash
cd redis-search-django/example
```

#### Install requirements:

```bash
pip install -r requirements.txt
```

#### run migrations and runserver:

```bash
python manage.py migrate
python manage.py runserver       # http://127.0.0.1:8000/
```
