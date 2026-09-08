# Установка экосистемы и бота MAX на Reg.ru

Инструкция для человека, который раньше **не поднимал сайты на сервере**.  
Идите **по шагам сверху вниз**. Не пропускайте шаг, даже если кажется «очевидным».

В этой инструкции два разных места, где вы пишете команды:

1. **Терминал Cursor на вашем компьютере** (Windows) — подключение к серверу, копирование файлов.
2. **Терминал уже на сервере** — после команды `ssh`. Приглашение меняется с `PS C:\...>` на что-то вроде `root@vm:~#`.

Подставляйте свои значения вместо:

- `ВАШ_IP` — публичный IP сервера из панели Reg.ru
- `ВАШ_ДОМЕН` — например `office.alfasklad.com` (без `https://`)
- `ВАШ_ПАРОЛЬ_ROOT` — пароль root, который выдаст Reg.ru

---

## План очерёдности

| Шаг | Что делаете | Зачем |
|-----|-------------|--------|
| 0 | Понять, что покупать | Не купить «хостинг сайтов» вместо сервера |
| 1 | Купить домен и VPS | Появится IP и Ubuntu |
| 2 | Подключиться по SSH из Cursor | Дальше все команды идут на сервер |
| 3 | Подготовить Ubuntu | Python, nginx, сертификат |
| 4 | Загрузить проект | Код сайта и бота на сервере |
| 5 | Установить зависимости | Flask, бот, PDF |
| 6 | Запустить сайт как службу | Сайт работает постоянно, не только пока открыт терминал |
| 7 | Привязать домен и HTTPS | Сайт открывается как обычный адрес в браузере |
| 8 | Запустить бота MAX как службу | Бот слушает MAX круглосуточно |
| 9 | Связать в админке | Токен, снять тест, ID механиков |
| 10 | Проверки | Убедиться, что сайт и бот живы |
| 11 | Обновление | Как выкладывать новые правки с компьютера |

---

## Шаг 0. Что покупать и чего не покупать

Это **не** обычный сайт на PHP. Нужны **два постоянно работающих процесса**:

1. **Сайт** (Flask) — личный кабинет, админка, автопарк, отчёты.
2. **Бот MAX** — отдельная программа, которая всё время спрашивает MAX: «есть новые сообщения?» (polling).

Из-за бота **не подойдёт**:

- «Хостинг» / «Виртуальный хостинг» / «Хостинг сайтов»
- «Конструктор сайтов»
- «Почта» как тариф
- ISPmanager «просто залить файлы в папку сайта» без своего сервера

Нужен **облачный VPS/VDS** (виртуальный сервер с Ubuntu), где вы сами запускаете программы.

### Какой тариф выбрать (актуально на август 2026, цены проверяйте на сайте)

Страница: [https://www.reg.ru/vps/](https://www.reg.ru/vps/)

| Тариф | Ресурсы | Ориентир цены | Берите? |
|-------|---------|---------------|---------|
| **Std C1-M1-D10** | 1 CPU, 1 ГБ RAM, 10 ГБ диск | ~390 ₽/мес | **Нет.** Мало памяти и места под фото/PDF |
| **Std C2-M2-D40** | 2 CPU, 2 ГБ RAM, 40 ГБ диск | ~980 ₽/мес | **Да, стартовый выбор** |
| Std C3-M3-D60 | 3 CPU, 3 ГБ RAM, 60 ГБ диск | ~1 470 ₽/мес | Если фото и отчёты быстро растут |
| High / GPU | дороже | — | Не нужно |

**Берите: Std C2-M2-D40.**

Дополнительно в форме заказа:

- **Регион:** Москва (или тот, что ближе к вам)
- **ОС:** **Ubuntu 24.04 LTS** (не Windows)
- **Панель ISPmanager:** **не ставить** (для этой инструкции она мешает)
- **Приложения Django/Docker/GitLab:** **не ставить**
- **Публичный IPv4:** включён (обычно даётся сразу)
- **Резервные копии:** включите, если есть галочка (не обязательно в первый день)

Отдельно купите **домен**, если его ещё нет: в том же кабинете Reg.ru → Домены.  
Для теста можно сначала открывать сайт по IP, домен привязать на шаге 7.

---

## Шаг 1. Заказ в кабинете Reg.ru (без команд)

1. Зайдите на [https://www.reg.ru](https://www.reg.ru) и войдите в аккаунт.
2. Откройте [https://www.reg.ru/vps/](https://www.reg.ru/vps/).
3. Выберите **Std C2-M2-D40**, Ubuntu 24.04, без панели.
4. Оплатите. Через 1–2 минуты сервер появится в кабинете.
5. Откройте карточку сервера и **запишите в блокнот**:
   - IP-адрес
   - логин: обычно `root`
   - пароль root (или скачайте SSH-ключ, если вы его указали)
6. Если домен уже есть: Домены → ваш домен → DNS → запись типа **A**:
   - имя: `@` (и при желании `www`)
   - значение: `ВАШ_IP`
   - подождите 10–60 минут (иногда до суток)

Пока DNS не обновился, сайт по домену не откроется. По IP — откроется после шага 6.

---

## Шаг 2. Подключение из терминала Cursor (ваш компьютер)

Откройте в Cursor: **Terminal → New Terminal**.  
Должен быть PowerShell.

Проверка, что SSH есть:

```powershell
ssh -V
```

Должна появиться версия OpenSSH. Если ошибка «не является командой» — в Windows: Параметры → Приложения → Дополнительные компоненты → OpenSSH Client.

Подключение к серверу:

```powershell
ssh root@ВАШ_IP
```

Первый раз спросит `Are you sure you want to continue connecting?` — напишите `yes` и Enter.

Дальше введите пароль root. **Символы не отображаются — это нормально.** Enter.

Если вошли, приглашение станет похоже на:

```text
root@xxxx:~#
```

Все команды **шагов 3–8 и 10** вводите **уже здесь**, на сервере.  
Чтобы выйти с сервера на компьютер:

```bash
exit
```

---

## Шаг 3. Подготовка Ubuntu (на сервере)

Скопируйте блок целиком, вставьте в терминал, Enter:

```bash
apt update
apt upgrade -y
apt install -y python3 python3-venv python3-pip python3-dev build-essential
apt install -y git nginx unzip ufw curl
apt install -y libjpeg-dev zlib1g-dev libfreetype6-dev
apt install -y libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 shared-mime-info
apt install -y certbot python3-certbot-nginx
```

Часовой пояс (Москва):

```bash
timedatectl set-timezone Europe/Moscow
```

Файрвол: разрешаем SSH, сайт и HTTPS. **22 порт не закрывайте — иначе потеряете доступ.**

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status
```

Должны быть `22`, `80`, `443` в статусе ALLOW.

---

## Шаг 4. Загрузить проект на сервер

Сделайте папку:

```bash
mkdir -p /opt/mechanic_report
```

Дальше выберите **один** способ: А (проще с вашего диска) или Б (если проект уже на GitHub).

### Способ А. Скопировать с компьютера (рекомендуется в первый раз)

**На сервере** временно выйдите:

```bash
exit
```

**В терминале Cursor на компьютере** (путь подставьте свой, если проект лежит иначе):

```powershell
cd C:\diproject\OFFICEMACHANIC\mechanic_report
tar --exclude=venv --exclude=__pycache__ --exclude=.git --exclude=android-notebook --exclude=data\app.db --exclude=data\uploads -cvf mechanic_deploy.tar .
scp mechanic_deploy.tar root@ВАШ_IP:/opt/mechanic_report/
```

Если `tar` в PowerShell ругается, запакуйте вручную:

1. В проводнике откройте `C:\diproject\OFFICEMACHANIC\mechanic_report`
2. Выделите всё **кроме** папок `venv`, `android-notebook`, `.git`
3. Отправьте в ZIP `mechanic_deploy.zip`
4. В терминале Cursor:

```powershell
scp C:\Users\ВАШ_ПОЛЬЗОВАТЕЛЬ\Desktop\mechanic_deploy.zip root@ВАШ_IP:/opt/mechanic_report/
```

Снова зайдите на сервер:

```powershell
ssh root@ВАШ_IP
```

Распакуйте (выберите ту команду, которая совпадает с файлом):

```bash
cd /opt/mechanic_report
tar -xvf mechanic_deploy.tar
```

или:

```bash
cd /opt/mechanic_report
unzip mechanic_deploy.zip
```

Проверка, что файлы на месте:

```bash
ls -la /opt/mechanic_report
```

Должны быть видны `app.py`, `requirements.txt`, папка `max_bot`.

### Способ Б. Если код уже в Git-репозитории

На сервере:

```bash
cd /opt
git clone АДРЕС_ВАШЕГО_РЕПОЗИТОРИЯ mechanic_report
```

---

## Шаг 5. Установить Python-пакеты (на сервере)

```bash
cd /opt/mechanic_report
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
```

Проверка, что Flask ставится:

```bash
python -c "import flask; import maxapi; print('ok', flask.__version__)"
```

Должно напечатать `ok` и номер версии. Если ошибка — скопируйте её целиком, не идите дальше.

Сменить секрет сессий сайта (обязательно):

```bash
python3 - << 'PY'
import secrets, pathlib
p = pathlib.Path("/opt/mechanic_report/app.py")
text = p.read_text(encoding="utf-8")
old = 'app.secret_key = "change_this_secret_key"'
new = 'app.secret_key = "' + secrets.token_hex(32) + '"'
if old not in text:
    raise SystemExit("Не найдена строка secret_key — откройте app.py и замените ключ вручную")
p.write_text(text.replace(old, new, 1), encoding="utf-8")
print("secret_key обновлён")
PY
```

Папки данных:

```bash
mkdir -p /opt/mechanic_report/data/uploads/photos
mkdir -p /opt/mechanic_report/data/uploads/fleet_photos
mkdir -p /opt/mechanic_report/data/uploads/mechanic_info
mkdir -p /opt/mechanic_report/data/bot_uploads
```

Пользователь, от которого будут крутиться сайт и бот:

```bash
id www-data >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin www-data
chown -R www-data:www-data /opt/mechanic_report
```

---

## Шаг 6. Сайт как постоянная служба (gunicorn + systemd)

Создайте файл службы:

```bash
cat >/etc/systemd/system/mechanic-web.service << 'EOF'
[Unit]
Description=Mechanic report website (Flask/gunicorn)
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/mechanic_report
Environment=PORT=8000
ExecStart=/opt/mechanic_report/venv/bin/gunicorn --bind 127.0.0.1:8000 --workers 2 --timeout 120 app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

Запуск:

```bash
systemctl daemon-reload
systemctl enable mechanic-web
systemctl start mechanic-web
systemctl status mechanic-web --no-pager
```

В статусе должно быть **active (running)** зелёным. Если **failed** — сразу:

```bash
journalctl -u mechanic-web -n 80 --no-pager
```

Проверка с самого сервера:

```bash
curl -I http://127.0.0.1:8000/login
```

Ожидается строка `HTTP/1.1 200` (или `302`). Не `000` и не `Connection refused`.

Пока nginx не настроен, с телефона/браузера сайт ещё не откроется по домену — это следующий шаг.

---

## Шаг 7. Домен, nginx и HTTPS

Подставьте свой домен **в двух местах** ниже (`server_name` и потом certbot).

```bash
cat >/etc/nginx/sites-available/mechanic << 'EOF'
server {
    listen 80;
    server_name ВАШ_ДОМЕН www.ВАШ_ДОМЕН;

    client_max_body_size 32m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
EOF
```

Если файла ещё нет с вашим доменом, проще открыть редактор и вписать домен руками:

```bash
nano /etc/nginx/sites-available/mechanic
```

Замените `ВАШ_ДОМЕН` на реальный, сохраните: `Ctrl+O`, Enter, `Ctrl+X`.

Включить сайт в nginx:

```bash
ln -sf /etc/nginx/sites-available/mechanic /etc/nginx/sites-enabled/mechanic
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
```

Проверка по IP (с компьютера, в браузере):

```text
http://ВАШ_IP/login
```

Должна открыться страница входа.

Сертификат HTTPS (когда DNS домена уже указывает на IP):

```bash
certbot --nginx -d ВАШ_ДОМЕН -d www.ВАШ_ДОМЕН
```

Следуйте вопросам: email, согласие, редирект на HTTPS — **2** (Redirect).

Дальше сайт открывайте так:

```text
https://ВАШ_ДОМЕН/login
```

### Первый вход в админку

Если база на сервере новая, логин и пароль по умолчанию:

- логин: `admin`
- пароль: `admin`

Сразу после входа: **Сотрудники** → Редактировать администратора → **смените пароль**.  
Не оставляйте `admin/admin` в интернете.

---

## Шаг 8. Бот MAX как постоянная служба

Бот **не умеет** работать, пока в админке включён «Тестовый режим» и нет токена.  
Поэтому сначала **шаг 9 (пункты 1–3)**, потом возвращайтесь сюда и запускайте службу.

Файл службы (можно создать заранее):

```bash
cat >/etc/systemd/system/mechanic-maxbot.service << 'EOF'
[Unit]
Description=MAX bot polling for mechanic report
After=network.target mechanic-web.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/mechanic_report
ExecStart=/opt/mechanic_report/venv/bin/python -m max_bot.run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Когда токен введён и тестовый режим снят:

```bash
systemctl daemon-reload
systemctl enable mechanic-maxbot
systemctl start mechanic-maxbot
systemctl status mechanic-maxbot --no-pager
```

Должно быть **active (running)**. В логе:

```bash
journalctl -u mechanic-maxbot -n 50 --no-pager
```

Нормальная строка: `Запуск polling MAX…`  
Плохие строки:

- `Задайте max_bot_token` — токен не сохранён в админке
- `bot_test_mode=1` — галочка «Тестовый режим» ещё стоит

После исправления:

```bash
systemctl restart mechanic-maxbot
```

---

## Шаг 9. Как связать сайт и бота (в браузере)

Сайт и бот читают **одну базу** `data/app.db` на этом сервере. Отдельно «прописывать IP бота» не нужно. Связка такая:

```text
MAX  →  бот (служба mechanic-maxbot)  →  та же SQLite, что и сайт
Вы   →  https://ВАШ_ДОМЕН            →  gunicorn  →  та же SQLite
```

### 9.1. Токен MAX

1. В кабинете MAX создайте бота и скопируйте **токен**.
2. На сайте: войдите как админ → **Настройки** → **Бот для МАКС**.
3. Вставьте токен → Сохранить.

### 9.2. Снять тестовый режим

В том же окне **снимите** галочку «Тестовый режим» → Сохранить.  
Иначе служба бота сразу завершится с кодом ошибки.

### 9.3. Секрет экосистемы

На той же странице есть поле **секрет экосистемы**. Его сайт создаёт сам.  
Вручную никуда копировать не обязательно, если бот запущен командой `python -m max_bot.run` на **этом же** сервере.  
Нужен он только если какой-то внешний сервис вызывает `POST /api/bot/verify`.

### 9.4. Яндекс.Диск (по желанию)

Настройки → **Сопряжение с Яндекс Диском** → OAuth-токен.  
После подтверждения подписи скан уходит в папку:

```text
{корневая_папка_на_Диске}/REPORTS/{ФИО}/{дд_MM_гггг}/
```

### 9.5. Привязка механиков к MAX

У каждого механика в **Сотрудники → Редактировать** заполните **ID пользователя в MAX**.  
Без этого бот не поймёт, чей пост.

### 9.6. Как механик пользуется ботом

1. Механик публикует в MAX **фото всего листа отчёта**.
2. В подписи пишет хештег серийного номера, например `#0002884`.
3. Бот проверяет подпись на бланке и пишет результат.
4. В админке отчёт появляется, когда механик нажал «Подписан».

Справка по серийнику в чате MAX: команда из файла `max_bot/README.md` (карточка оборудования).

### 9.7. После сохранения настроек

```bash
systemctl restart mechanic-maxbot
systemctl status mechanic-maxbot --no-pager
```

---

## Шаг 10. Команды проверки работоспособности

Все команды ниже — **на сервере** (`ssh root@ВАШ_IP`), кроме тех, где явно написано «на компьютере».

### 10.1. Живы ли службы

```bash
systemctl is-active mechanic-web
systemctl is-active mechanic-maxbot
systemctl is-active nginx
```

Ожидание: три раза `active`.

### 10.2. Сайт отвечает локально

```bash
curl -I http://127.0.0.1:8000/login
curl -I http://127.0.0.1/login
```

Ожидание: `200` или `302`.

### 10.3. Сайт отвечает из интернета

**На компьютере** в терминале Cursor:

```powershell
curl -I http://ВАШ_IP/login
curl -I https://ВАШ_ДОМЕН/login
```

В браузере откройте `https://ВАШ_ДОМЕН/login` и войдите.

### 10.4. Процессы Python

```bash
ps aux | grep -E 'gunicorn|max_bot' | grep -v grep
```

Должны быть строки `gunicorn` и `python -m max_bot.run`.

### 10.5. Порты

```bash
ss -tlnp | grep -E ':80|:443|:8000'
```

- `:8000` слушает только `127.0.0.1` (не весь интернет) — так и должно быть
- `:80` и `:443` слушает nginx

### 10.6. База создалась

```bash
ls -lh /opt/mechanic_report/data/app.db
```

Файл должен существовать и расти после работы в админке.

### 10.7. Ручной тест бота без MAX (на сервере)

Только если хотите проверить распознавание картинки, не трогая MAX:

```bash
cd /opt/mechanic_report
source venv/bin/activate
python -m max_bot.cli_test --help
```

Боевой бот при этом должен быть запущен службой `mechanic-maxbot`, а не второй копией в этом же терминале.

### 10.8. Логи, если что-то сломалось

Сайт:

```bash
journalctl -u mechanic-web -n 100 --no-pager
```

Бот:

```bash
journalctl -u mechanic-maxbot -n 100 --no-pager
```

Nginx:

```bash
tail -n 50 /var/log/nginx/error.log
```

---

## Шаг 11. Как выкладывать обновления с компьютера

Когда поправили код в Cursor и хотите обновить боевой сайт.

**На компьютере:**

```powershell
cd C:\diproject\OFFICEMACHANIC\mechanic_report
scp app.py vehicle_fleet.py root@ВАШ_IP:/opt/mechanic_report/
scp -r templates static max_bot integrations root@ВАШ_IP:/opt/mechanic_report/
```

Или снова упаковать весь проект, как в шаге 4.

**На сервере:**

```bash
chown -R www-data:www-data /opt/mechanic_report
systemctl restart mechanic-web
systemctl restart mechanic-maxbot
systemctl status mechanic-web mechanic-maxbot --no-pager
```

**Не затирайте** на сервере папку `data/` — там база, фото и загрузки.

Если меняли `requirements.txt`:

```bash
cd /opt/mechanic_report
source venv/bin/activate
pip install -r requirements.txt
systemctl restart mechanic-web
systemctl restart mechanic-maxbot
```

---

## Что должно получиться в итоге

| Что | Как работает |
|-----|----------------|
| Сайт | `https://ВАШ_ДОМЕН/login` → nginx → gunicorn на порту 8000 |
| Админка | тот же сайт, раздел «Сотрудники / Автопарк / Настройки» |
| Бот MAX | служба `mechanic-maxbot`, читает токен из настроек сайта |
| Данные | файл `/opt/mechanic_report/data/app.db` |

Сайт и бот — **два разных процесса**, но **одна установка** и **одна база**.  
Не ставьте бота на домашний компьютер, а сайт на Reg.ru: они разъедутся.

---

## Частые ошибки

| Симптом | Что сделать |
|---------|-------------|
| `ssh: connect to host ... timed out` | В панели Reg.ru сервер включён? IP верный? Порт 22 открыт? |
| `Permission denied` при ssh | Неверный пароль. Сбросьте root в панели VPS |
| `mechanic-web failed` | `journalctl -u mechanic-web -n 80 --no-pager` |
| Сайт по домену не открывается | DNS A-запись ещё не обновилась. Проверьте `ping ВАШ_ДОМЕН` |
| `certbot` не выпускает сертификат | Домен должен уже указывать на IP. Подождите DNS |
| Бот сразу останавливается | Нет токена или включён тестовый режим. Сохраните настройки, `systemctl restart mechanic-maxbot` |
| 502 Bad Gateway | Сайт-служба упала: `systemctl restart mechanic-web` |
| После копирования файлов 500 | Права: `chown -R www-data:www-data /opt/mechanic_report` |

---

## Мини-шпаргалка команд на каждый день

Зайти:

```powershell
ssh root@ВАШ_IP
```

Статус:

```bash
systemctl status mechanic-web mechanic-maxbot nginx --no-pager
```

Перезапуск сайта и бота:

```bash
systemctl restart mechanic-web
systemctl restart mechanic-maxbot
```

Логи бота «вживую»:

```bash
journalctl -u mechanic-maxbot -f
```

Выход: `Ctrl+C`, потом `exit`.
