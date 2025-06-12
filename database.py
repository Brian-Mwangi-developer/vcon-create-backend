from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
load_dotenv()

URL_DATABASE = os.environ.get("DATABASE_URL")
if not URL_DATABASE:
    raise ValueError("DATABASE_URL is not set in the environment.")


engine = create_engine(URL_DATABASE)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
