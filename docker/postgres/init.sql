-- Runs once, when the database volume is first created.
-- Two roles: the owner runs migrations and owns the tables; the app role is what
-- the API uses, and row-level security applies to it.
-- These passwords are for local development only.

CREATE ROLE farmulator_owner LOGIN PASSWORD 'farmulator_owner';
CREATE ROLE farmulator_app LOGIN PASSWORD 'farmulator_app';

CREATE DATABASE farmulator OWNER farmulator_owner;

\connect farmulator
GRANT USAGE ON SCHEMA public TO farmulator_app;
