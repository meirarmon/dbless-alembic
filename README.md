## dbless-alembic
Instead of having a production-like DB locally, or connecting with Alembic
to a production environment in order to create migrations, you can run this
script which will create a DB in a Docker container, will run the migration
to head, and will create the new migration based on the difference of the 
models that you have and the DB itself. It will finish by tearing all of this
down.

### How to configure?
* Install Alembic, and configure it
* In alembic.ini add a new section called "dbless"
* add the following configuration to the new section:
  * image_name - Docker image name to use for the DB
  * container_env - arguments that will pass as env to the container. Use the
    format key1=value1; key2=value2;
  * port_mapping - ports to map in the container. Use the format 
    remote1=local1; remote2=local2;
  * engine_url - the engine url to use to connect to the DB in the container

### Example configuration:
```
[dbless]
image_name = postgres:14.5-alpine
container_env = POSTGRES_PASSWORD=123456;
port_mapping = 5432/tcp=5432;
engine_url = postgresql+psycopg2://postgres:123456@127.0.0.1/postgres
```
