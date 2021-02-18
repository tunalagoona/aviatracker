import json
import logging
import time
from datetime import date
from typing import Dict, List

from psycopg2 import connect, extras

from flighttracker.database import (
    Airport,
    AirportStats,
    column_value_to_str,
    FlightAirportInfo,
    FlightPath,
    StateVector,
)
from flighttracker.opensky import OpenskyStates

logger = logging.getLogger()


class DB:
    def __init__(self, name: str, user: str, password: str, host: str, port: int) -> None:
        logger.info(f"Connecting to the PostgreSQL database - {user}@{host}:{port}/{name}")
        self.conn = connect(dbname=name, user=user, password=password, host=host, port=port)
        with self.conn:
            with self.conn.cursor() as curs:
                curs.execute("SELECT 1;")
        logger.info(f"Connected to the Postgres database - {user}@{host}:{port}/{name}")

    def insert_airports(self, airports: List[Airport]) -> None:
        with self.conn:
            with self.conn.cursor() as curs:
                columns_str, values_str = column_value_to_str(Airport._fields)
                insert_query = f"INSERT INTO airports ({columns_str}) VALUES %s"
                template = f"({columns_str})"
                extras.execute_values(curs, insert_query, airports, template)

    def insert_current_states(self, vectors: List[StateVector]) -> None:
        with self.conn:
            for vector in vectors:
                row = dict(vector)
                columns_str, values_str = column_value_to_str(vector._fields)
                with self.conn.cursor() as curs:
                    """Row deletion can be implemented either by DELETE or by TRUNCATE TABLE.
                    Though the DELETE option requires VACUUM to remove dead row versions,
                    it is still preferred in our context as TRUNCATE would violate MVCC semantics
                    and hinder parallel requests to access current states"""

                    curs.execute(
                        f"DELETE FROM current_states;"
                        f"INSERT INTO current_states ({columns_str}) VALUES ({values_str})",
                        row,
                    )

    def get_current_states(self) -> List[Dict] or None:
        with self.conn:
            with self.conn.cursor() as curs:
                curs.execute("SELECT * FROM current_states")
                vectors = curs.fetchall()
                state_vectors = []
                for vector in vectors:
                    state_vectors.append(dict(StateVector(*vector)))
                return state_vectors

    def insert_new_path(self, path: FlightPath) -> None:
        columns_str, values_str = column_value_to_str(path._fields)
        with self.conn.cursor() as curs:
            curs.execute(
                f"INSERT INTO flight_paths ({columns_str})" f"VALUES ({values_str})",
                dict(path),
            )

    def update_paths(self) -> None:
        with self.conn.cursor() as curs:
            curs.execute("SELECT * FROM current_states;")
            states = curs.fetchall()
            if len(states) > 0:
                time = states[0]

                api = OpenskyStates()
                airports_response: List[FlightAirportInfo] = api.get_airports(begin=time - 5, end=time + 5)

                for state in states:
                    state = StateVector(**state)

                    curs.execute(
                        "SELECT * FROM flight_paths" "WHERE icao24 = %s",
                        state["icao24"],
                    )
                    paths = curs.fetchall()

                    for flight in airports_response:
                        if flight["icao24"] == state["icao24"]:
                            departure_airport_icao = flight["estDepartureAirport"]
                            arrival_airport_icao = flight["estArrivalAirport"]
                            estimated_arr_time = flight["estArrivalTime"]

                            airport_icao = '"' + arrival_airport_icao + '"'
                            curs.execute(
                                "SELECT latitude, longitude FROM airports" "WHERE icao = %s",
                                airport_icao,
                            )
                            airport = Airport(*curs.fetchone())

                            arrival_airport_lat = airport["latitude"]
                            arrival_airport_long = airport["longitude"]
                            break

                    latest_update = 0
                    max_ind = 0
                    for i in range(0, len(paths)):
                        paths[i] = FlightPath(*paths[i])
                        if paths[i]["last_update"] > latest_update:
                            latest_update = paths[i]["last_update"]
                            max_ind = i

                    current_location = {
                        "longitude": state["longitude"],
                        "latitude": state["latitude"],
                    }
                    path = [json.dumps(current_location)]
                    if len(paths) == 0 or paths[max_ind][9] is True:
                        new_path = FlightPath(
                            last_update=state["request_time"],
                            icao24=state["icao24"],
                            departure_airport_icao=departure_airport_icao,
                            arrival_airport_icao=arrival_airport_icao,
                            arrival_airport_long=arrival_airport_long,
                            arrival_airport_lat=arrival_airport_lat,
                            estimated_arrival_time=estimated_arr_time,
                            path=path,
                            finished=False,
                        )
                        self.insert_new_path(new_path)

                    else:
                        curs.execute(
                            "UPDATE flight_paths"
                            f"SET path = path || %s::jsonb "
                            "WHERE icao24 = %s AND last_update = %s",
                            (path, state[1], latest_update),
                        )

    def update_stats_for_one_airport(self, airport_icao: str, is_arrival: bool) -> None:
        today = date.today().strftime("%b-%d-%Y")

        if is_arrival:
            quantity_of_new_arrivals = 1
            quantity_of_new_departures = 0
        else:
            quantity_of_new_arrivals = 0
            quantity_of_new_departures = 1

        with self.conn.cursor() as curs:
            curs.execute("SELECT * FROM airport_stats" "WHERE airport_icao = %s AND date = %s", (airport_icao, today))
            stats = AirportStats(*curs.fetchone())

            if len(stats) == 0:
                new_stats = AirportStats(
                    airport_icao=airport_icao,
                    date=today,
                    airplane_quantity_arrivals=quantity_of_new_arrivals,
                    airplane_quantity_departures=quantity_of_new_departures,
                )

                columns_str, values_str = column_value_to_str(new_stats._fields)

                curs.execute(
                    f"INSERT INTO airport_stats ({columns_str})" f"VALUES ({values_str})",
                    new_stats,
                )

            else:
                curs.execute(
                    "UPDATE airport_stats"
                    "SET airplane_quantity_arrivals = airplane_quantity_arrivals + 1"
                    "WHERE airport_icao = (%s) AND date_today = (%s)",
                    (stats["airport_icao"], stats["date"]),
                )

    def update_airport_stats(self) -> None:
        with self.conn.cursor() as curs:
            time_now = time.time()
            logger.info(f"time now = {time_now}")

            curs.execute("SELECT * FROM flight_paths" "WHERE ((%s) - last_update) < 3600 AND finished = True", time_now)
            paths = curs.fetchall()

            for path in paths:
                path = FlightPath(*path)
                arrival_airport = path["arrival_airport_icao"]
                departure_airport = path["departure_airport_icao"]

            self.update_stats_for_one_airport(arrival_airport)
            self.update_stats_for_one_airport(departure_airport)

    def execute_script(self, script: str) -> None:
        with self.conn:
            with self.conn.cursor() as curs:
                curs.execute(script)

    def close(self) -> None:
        self.conn.close()
