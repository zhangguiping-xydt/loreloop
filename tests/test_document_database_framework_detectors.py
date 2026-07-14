from __future__ import annotations

import pytest

from loreloop.knowledge.authoritative_detector_prisma import detect_prisma_schema
from loreloop.knowledge.authoritative_detector_python_database import (
    detect_python_database_models,
)
from loreloop.knowledge.authoritative_detector_python_migrations import (
    detect_python_migrations,
)
from loreloop.knowledge.authoritative_detector_typeorm import detect_typeorm_entities
from loreloop.knowledge.authoritative_records import DetectionError, ForeignKeyRecord


def test_sqlalchemy_declarative_detector_extracts_schema_without_importing_models() -> None:
    source = """
class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    api_token = Column(String(64), default="must-not-leak")
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    __table_args__ = (Index("ix_users_role", "role_id", unique=True),)
"""

    report = detect_python_database_models(source, "backend", "models.py")

    users = report.tables[1]
    assert tuple(table.name for table in report.tables) == ("roles", "users")
    assert users.primary_key == ("id",)
    assert users.columns[1].data_type == "String(64)"
    assert users.columns[1].default is None
    assert users.columns[2].name == "role_id"
    assert users.foreign_keys[0].referenced_table == "roles"
    assert report.indexes[0].columns == ("role_id",)
    assert report.indexes[0].unique is True
    assert "must-not-leak" not in repr(report)


def test_django_model_detector_resolves_in_file_table_and_field_names() -> None:
    source = """
class Role(models.Model):
    id = models.AutoField(primary_key=True)
    class Meta:
        db_table = "roles"

class User(models.Model):
    password = models.CharField(max_length=128, default="must-not-leak")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, db_column="role_fk")
    class Meta:
        db_table = "users"
        indexes = [models.Index(fields=["role"], name="ix_users_role")]
"""

    report = detect_python_database_models(source, ".", "accounts/models.py")

    users = report.tables[1]
    assert users.name == "users"
    assert users.columns[0].data_type == "CharField(max_length=128)"
    assert users.columns[0].default is None
    assert users.foreign_keys[0].columns == ("role_fk",)
    assert users.foreign_keys[0].referenced_table == "roles"
    assert report.indexes[0].columns == ("role_fk",)
    assert "must-not-leak" not in repr(report)


def test_alembic_migration_detector_aggregates_common_operations() -> None:
    source = """
def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("api_token", sa.String(), server_default="must-not-leak"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_foreign_key("fk_users_role", "users", "roles", ["role_id"], ["id"])
    op.create_index("ix_users_role", "users", ["role_id"], unique=True)
"""

    report = detect_python_migrations(source, ".", "alembic/versions/001_users.py")

    users = report.tables[0]
    assert users.name == "users"
    assert users.primary_key == ("id",)
    assert tuple(column.name for column in users.columns) == ("id", "role_id", "api_token")
    assert users.columns[2].default is None
    assert users.foreign_keys[0].referenced_table == "roles"
    assert report.indexes[0].unique is True
    assert "must-not-leak" not in repr(report)


def test_django_migration_detector_extracts_create_add_field_and_index() -> None:
    source = """
class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                ("id", models.BigAutoField(primary_key=True)),
                ("password", models.CharField(default="must-not-leak", max_length=128)),
                ("role", models.ForeignKey(to="roles.Role", on_delete=CASCADE)),
            ],
            options={"db_table": "users"},
        ),
        migrations.AddField(
            model_name="users",
            name="enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddIndex(
            model_name="users",
            index=models.Index(fields=["role_id"], name="ix_users_role"),
        ),
    ]
"""

    report = detect_python_migrations(source, ".", "accounts/migrations/0001_initial.py")

    users = report.tables[0]
    assert users.name == "users"
    assert tuple(column.name for column in users.columns) == (
        "id",
        "password",
        "role_id",
        "enabled",
    )
    assert users.foreign_keys[0].referenced_table == "roles.Role"
    assert report.indexes[0].columns == ("role_id",)
    assert "must-not-leak" not in repr(report)


def test_prisma_detector_extracts_mapped_tables_columns_relations_and_indexes() -> None:
    source = """
model Role {
  id    Int    @id
  users User[]
  @@map("roles")
}

model User {
  id       Int    @id @map("user_id")
  password String @default("must-not-leak")
  roleId   Int    @map("role_id")
  role     Role   @relation(fields: [roleId], references: [id])
  @@index([roleId], map: "ix_users_role")
  @@map("users")
}
"""

    report = detect_prisma_schema(source, "database", "prisma/schema.prisma")

    roles, users = report.tables
    assert tuple(column.name for column in roles.columns) == ("id",)
    assert users.name == "users"
    assert users.primary_key == ("user_id",)
    assert users.columns[1].default is None
    assert users.foreign_keys[0].columns == ("role_id",)
    assert users.foreign_keys[0].referenced_table == "roles"
    assert report.indexes[0].columns == ("role_id",)
    assert "must-not-leak" not in repr(report)


def test_prisma_detector_rejects_an_unclosed_explicit_model() -> None:
    with pytest.raises(DetectionError, match="closing brace"):
        _ = detect_prisma_schema("model User {\n id Int @id\n", ".", "schema.prisma")


def test_typeorm_detector_extracts_entities_relations_and_named_indexes() -> None:
    source = """
@Entity({ name: "roles" })
export class Role {
  @PrimaryGeneratedColumn()
  id!: number;
}

@Entity("users")
@Index("ix_users_email", ["email"], { unique: true })
export class User {
  @PrimaryGeneratedColumn({ name: "user_id" })
  id!: number;

  @Column({ name: "api_token", type: "varchar", default: "must-not-leak" })
  apiToken!: string;

  @Column({ type: "varchar", nullable: false })
  email!: string;

  @ManyToOne(() => Role)
  @JoinColumn({ name: "role_id", referencedColumnName: "id" })
  role!: Role;
}
"""

    report = detect_typeorm_entities(source, "backend", "src/entities.ts")

    users = report.tables[1]
    assert users.name == "users"
    assert users.primary_key == ("user_id",)
    assert users.columns[1].default is None
    assert users.foreign_keys[0] == ForeignKeyRecord(("role_id",), "roles", ("id",))
    assert report.indexes[0].columns == ("email",)
    assert report.indexes[0].unique is True
    assert "must-not-leak" not in repr(report)
