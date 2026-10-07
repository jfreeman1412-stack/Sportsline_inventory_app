# Moving the inventory app to the Sytist droplet

The inventory app runs as two containers (`inventory-app` and `inventory-poller`) next to Sytist. Like the delivery and campaigns apps, it publishes no ports. The `sytist` container's Apache serves `inventory.sportslinephotography.com` and forwards it to the app over the `sytist_docker_default` network. The app reaches MySQL there as `mysql`.

None of these steps restart or rebuild the `sytist` container, so the photo site stays up throughout. Run everything as root on the droplet unless a step says otherwise.

## 0. Before you start (recommended): close the public MySQL port

MySQL is currently published on `0.0.0.0:3306`, which Docker exposes past ufw. Nothing outside the server needs it: Sytist, phpMyAdmin and the apps all connect by container name.

1. In `/home/sytist_docker/docker-compose.yml`, change `- "3306:3306"` to `- "127.0.0.1:3306:3306"`.
2. Run `cd /home/sytist_docker && docker compose up -d mysql`. This restarts only MySQL, so the photo site loses its database for about 10 seconds.
3. Any desktop tool that connected straight to port 3306 now needs an SSH tunnel: `ssh -L 3306:127.0.0.1:3306 sytist`.

## 1. DNS and certificate

1. In Cloudflare, add a **proxied** (orange cloud) `A` record: `inventory` → `24.199.107.201`.
2. Check that the origin certificate covers the new name:
   ```
   openssl x509 -in /home/sytist_docker/apache_ssl/cloudflare.crt -noout -ext subjectAltName
   ```
   It should list `*.sportslinephotography.com`. If it only lists specific names, create a new origin certificate in Cloudflare that includes `inventory.sportslinephotography.com` before step 6.

## 2. Get the code

```
git clone https://github.com/jfreeman1412-stack/Sportsline_inventory_app.git /home/sportsline-inventory
cd /home/sportsline-inventory
```

The repository is private, so the clone needs a GitHub login. Use the same method the other apps on this droplet use, or a read-only deploy key.

## 3. Create the database and logins

Open a MySQL prompt as root (the root password is in `/home/sytist_docker/.env`):

```
docker exec -it mysql mysql -uroot -p
```

Check the Sytist database name and that its order tables are there. `SYTIST_DB` below stands for the value of `SYTIST_DB_NAME` in `/home/sytist_docker/.env`.

```sql
SHOW TABLES FROM SYTIST_DB LIKE 'ms_%order%';
```

Create the app's own database and two logins, with your own strong passwords (`openssl rand -hex 24`):

```sql
CREATE DATABASE sportsline_inventory CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER 'inventory_app'@'%' IDENTIFIED BY 'CHANGE-ME-1';
GRANT SELECT, INSERT, UPDATE, DELETE ON sportsline_inventory.* TO 'inventory_app'@'%';

-- Read-only access to just the Sytist tables the sync reads
CREATE USER 'inventory_reader'@'%' IDENTIFIED BY 'CHANGE-ME-2';
GRANT SELECT ON SYTIST_DB.ms_order_status_logs TO 'inventory_reader'@'%';
GRANT SELECT ON SYTIST_DB.ms_cart TO 'inventory_reader'@'%';
GRANT SELECT ON SYTIST_DB.ms_cart_options TO 'inventory_reader'@'%';
GRANT SELECT ON SYTIST_DB.ms_photo_products TO 'inventory_reader'@'%';
EXIT;
```

`'%'` is fine here because the logins only work from inside the Docker network once step 0 is done.

Load the tables:

```
docker exec -i mysql mysql -uroot -p sportsline_inventory < db/init_app_schema.sql
```

**Bringing over existing inventory data?** If the app already ran somewhere else with real data, load that database instead of the empty schema. On the old machine, run `mysqldump sportsline_inventory > inventory.sql`, copy the file to the droplet, and run `docker exec -i mysql mysql -uroot -p sportsline_inventory < inventory.sql`.

## 4. Settings file

```
cp deploy/droplet/env.droplet.example .env
chmod 600 .env
nano .env
```

Fill in both passwords from step 3, the Sytist database name, SMTP and ShipStation details, and fresh values from `openssl rand -hex 32` for `INTERNAL_SYNC_TOKEN` and `SESSION_SECRET`.

## 5. Start the app and create your login

```
docker compose -f docker-compose.droplet.yml up -d --build
docker compose -f docker-compose.droplet.yml logs --tail 50
```

The poller should log `Starting poller` with no connection errors.

Create the Owner account **before** the site goes public in step 6. Until the first account exists, anyone who reaches the site could register as Owner.

```
docker exec -it inventory-app python - <<'PY'
from backend.app.auth import hash_password
from backend.app.database import SessionLocal
from backend.app.models import RoleEnum, User
with SessionLocal() as db:
    db.add(User(email="YOUR-EMAIL", password_hash=hash_password("YOUR-PASSWORD"), role=RoleEnum.owner))
    db.commit()
PY
```

## 6. Turn on the subdomain

```
cp deploy/droplet/zz-inventory.conf /home/sytist_docker/apache-live/sites-enabled/
docker exec sytist apache2ctl configtest
```

Only continue if it says `Syntax OK`. Then reload Apache without dropping visitors:

```
docker exec sytist apache2ctl graceful
```

Open https://inventory.sportslinephotography.com and log in. Then add the rest of the team under **Settings**.

If ShipStation is set up to send label webhooks to the old address, point them at `https://inventory.sportslinephotography.com/shipstation/label`.

## Updating later

```
cd /home/sportsline-inventory
git pull
docker compose -f docker-compose.droplet.yml up -d --build
```

## Undoing it

```
rm /home/sytist_docker/apache-live/sites-enabled/zz-inventory.conf
docker exec sytist apache2ctl graceful
cd /home/sportsline-inventory && docker compose -f docker-compose.droplet.yml down
```

The `sportsline_inventory` database stays in MySQL until you drop it.

## Memory

The droplet already uses swap. The app is capped at 512 MB and the poller at 256 MB (`mem_limit` in `docker-compose.droplet.yml`). Check with `docker stats --no-stream inventory-app inventory-poller`.
