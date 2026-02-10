"""Composable query builder for custom projection rules.

Phase 3 of the Adaptive Projection System (Decision 10). Translates
declarative projection rules into parameterized SQL queries using psycopg.sql
for safe composition — no string interpolation, no injection risk.

The builder collects query fragments (WHERE clauses, SELECT fields, GROUP BY
columns) and composes them into a final query in build().

Usage:
    builder = ProjectionQueryBuilder()
    query, params = (
        builder
        .from_events("sleep.logged")
        .for_user(user_id)
        .extract_fields(["hrv_rmssd", "deep_sleep_pct"])
        .group_by_week()
        .with_aggregations()
        .build()
    )
    await cur.execute(query, params)
"""

from __future__ import annotations

from psycopg import sql


class ProjectionQueryBuilder:
    """Build parameterized SQL queries from projection rules.

    Composable — each method returns self for chaining.
    build() returns (sql.Composed, params_tuple).
    """

    def __init__(self) -> None:
        self._event_types: list[str] = []
        self._user_id: str | None = None
        self._retracted_ids: list[str] = []
        self._fields: list[str] = []
        self._group_by_time_col: sql.Composable | None = None
        self._group_by_time_alias: str | None = None
        self._group_by_field: str | None = None
        self._aggregations: tuple[str, ...] = ()
        self._order_direction: str = "ASC"
        self._has_order: bool = False

    def from_events(self, *event_types: str) -> ProjectionQueryBuilder:
        """Filter by event type(s)."""
        self._event_types.extend(event_types)
        return self

    def for_user(self, user_id: str) -> ProjectionQueryBuilder:
        """Filter by user_id."""
        self._user_id = user_id
        return self

    def exclude_retracted(self, retracted_ids: set[str]) -> ProjectionQueryBuilder:
        """Exclude retracted event IDs from the query."""
        self._retracted_ids = list(retracted_ids)
        return self

    def extract_fields(self, fields: list[str]) -> ProjectionQueryBuilder:
        """Declare which JSON fields to extract from event data."""
        self._fields = list(fields)
        return self

    def group_by_day(self) -> ProjectionQueryBuilder:
        """Group results by day (date of timestamp)."""
        self._group_by_time_col = sql.SQL("(timestamp AT TIME ZONE 'UTC')::date")
        self._group_by_time_alias = "day"
        return self

    def group_by_week(self) -> ProjectionQueryBuilder:
        """Group results by ISO week."""
        self._group_by_time_col = sql.SQL(
            "to_char(timestamp AT TIME ZONE 'UTC', 'IYYY') || '-W' || "
            "to_char(timestamp AT TIME ZONE 'UTC', 'IW')"
        )
        self._group_by_time_alias = "week"
        return self

    def group_by_field(self, field: str) -> ProjectionQueryBuilder:
        """Group results by a JSON field value (e.g., supplement name)."""
        self._group_by_field = field
        return self

    def with_aggregations(
        self, aggs: tuple[str, ...] = ("avg", "min", "max", "count")
    ) -> ProjectionQueryBuilder:
        """Add aggregation functions for extracted fields."""
        self._aggregations = aggs
        return self

    def order_by_time(self, direction: str = "ASC") -> ProjectionQueryBuilder:
        """Order results by the time grouping column."""
        self._has_order = True
        self._order_direction = direction.upper()
        return self

    def build(self) -> tuple[sql.Composed, tuple]:
        """Compose the final SQL query from collected fragments.

        Returns (query, params) where query is a psycopg.sql.Composed
        and params is a tuple of parameter values.
        """
        if not self._fields:
            raise ValueError("No fields specified. Call extract_fields() first.")

        select_parts: list[sql.Composable] = []
        where_parts: list[sql.Composable] = []
        group_parts: list[sql.Composable] = []
        params: list = []
        param_idx = 0

        # --- Time grouping column ---
        if self._group_by_time_col is not None:
            alias = self._group_by_time_alias or "time_bucket"
            select_parts.append(
                sql.SQL("{expr} AS {alias}").format(
                    expr=self._group_by_time_col,
                    alias=sql.Identifier(alias),
                )
            )
            group_parts.append(self._group_by_time_col)

        # --- Category grouping ---
        if self._group_by_field is not None:
            field_expr = sql.SQL("data->>{}").format(sql.Literal(self._group_by_field))
            select_parts.append(
                sql.SQL("{expr} AS {alias}").format(
                    expr=field_expr,
                    alias=sql.Identifier(self._group_by_field),
                )
            )
            group_parts.append(field_expr)

        # --- Field extraction with aggregations ---
        if self._aggregations and group_parts:
            # Aggregated mode: apply agg functions
            for field in self._fields:
                # Skip the group_by field — it's already in SELECT
                if field == self._group_by_field:
                    continue
                cast_expr = sql.SQL("(data->>{})").\
                    format(sql.Literal(field))
                for agg in self._aggregations:
                    if agg == "count":
                        # Count non-null occurrences
                        select_parts.append(
                            sql.SQL("count({expr}) AS {alias}").format(
                                expr=cast_expr,
                                alias=sql.Identifier(f"{field}_{agg}"),
                            )
                        )
                    else:
                        # Numeric aggregation — cast to float
                        select_parts.append(
                            sql.SQL("{agg}(({expr})::float) AS {alias}").format(
                                agg=sql.SQL(agg),
                                expr=cast_expr,
                                alias=sql.Identifier(f"{field}_{agg}"),
                            )
                        )
        else:
            # Raw mode: just extract fields
            for field in self._fields:
                select_parts.append(
                    sql.SQL("data->>{} AS {}").format(
                        sql.Literal(field),
                        sql.Identifier(field),
                    )
                )
            # Always include timestamp in raw mode
            select_parts.append(sql.SQL("timestamp"))
            select_parts.append(sql.SQL("id"))

        # --- WHERE clauses ---
        if self._user_id is not None:
            where_parts.append(sql.SQL("user_id = %s"))
            params.append(self._user_id)

        if self._event_types:
            where_parts.append(sql.SQL("event_type = ANY(%s)"))
            params.append(self._event_types)

        if self._retracted_ids:
            where_parts.append(sql.SQL("id != ALL(%s)"))
            params.append(self._retracted_ids)

        # --- Compose ---
        query_parts = [
            sql.SQL("SELECT "),
            sql.SQL(", ").join(select_parts),
            sql.SQL(" FROM events"),
        ]

        if where_parts:
            query_parts.append(sql.SQL(" WHERE "))
            query_parts.append(sql.SQL(" AND ").join(where_parts))

        if group_parts:
            query_parts.append(sql.SQL(" GROUP BY "))
            query_parts.append(sql.SQL(", ").join(group_parts))

        if self._has_order and self._group_by_time_col is not None:
            direction = sql.SQL("ASC" if self._order_direction == "ASC" else "DESC")
            query_parts.append(sql.SQL(" ORDER BY "))
            query_parts.append(self._group_by_time_col)
            query_parts.append(sql.SQL(" "))
            query_parts.append(direction)

        return sql.Composed(query_parts), tuple(params)
