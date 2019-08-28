import logging
import datetime
import os

import jsonschema

from pubtools.pulplib._impl import compat_attr as attr
from ..schema import load_schema
from .common import InvalidDataException


LOG = logging.getLogger("pubtools.pulplib")


USER = os.environ.get("USER")
HOSTNAME = os.environ.get("HOSTNAME")


def iso_time_now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


@attr.s(kw_only=True, frozen=True)
class MaintenanceEntry(object):
    """Details about the maintenance status of a specific repository."""

    repo_id = attr.ib(type=str)
    """ID of repository in maintenance.
    Note: there is no guarantee that a repository of this ID currenly exists
          in the Pulp server."""
    message = attr.ib(default=None, type=str)
    """Why this repository is in maintenance."""
    owner = attr.ib(default=None, type=str)
    """Who set this repository in maintenance mode."""
    started = attr.ib(default=iso_time_now(), type=datetime.datetime)
    """:class:`~datetime.datetime` in UTC at when the maintenance started."""


@attr.s(kw_only=True, frozen=True)
class MaintenanceReport(object):
    """Represents the maintenance status of Pulp repositories.

    On release-engineering Pulp servers, it's possible to put individual repositories
    into "maintenance mode".  When in maintenance mode, external publishes of a repository
    will be blocked.  Other operations remain possible.

    This object holds information on the set of repositories currently in maintenance mode.
    """

    _OWNER = "%s@%s" % (USER, HOSTNAME) if all([USER, HOSTNAME]) else "pubtools.pulplib"

    _SCHEMA = load_schema("maintenance")

    last_updated = attr.ib(default=iso_time_now(), type=datetime.datetime)
    """:class:`~datetime.datetime` in UTC when this report was last updated,
    if it's the first time the report is created, current time is used."""

    last_updated_by = attr.ib(default=None, type=str)
    """Person/party who updated the report last time."""

    entries = attr.ib(default=attr.Factory(tuple), type=tuple)
    """A tuple of :class:`MaintenanceEntry` objects, indicating
    which repositories are in maintenance mode and details.
    If empty, then it means no repositories are in maintenance mode.
    """

    @entries.validator
    def check_duplicates(self, attribute, value):
        # pylint: disable=unused-argument
        # check if there's duplicate entries
        repo_ids = [entry.repo_id for entry in value]
        if len(repo_ids) != len(set(repo_ids)):
            raise ValueError("Duplicate entries")

    @classmethod
    def _from_data(cls, data):
        """Create a new report with raw data

        Args:
            data (dict):
                A dict containing a raw representation of the maintenance status.
        Returns:
            a new instance of ``cls``

        Raises:
            InvalidDataException
                If the provided ``data`` fails validation against an expected schema.
        """
        try:
            jsonschema.validate(instance=data, schema=cls._SCHEMA)
        except jsonschema.exceptions.ValidationError as error:
            LOG.exception("%s.from_data invoked with invalid data", cls.__name__)
            raise InvalidDataException(str(error))

        entries = []
        for repo_id, details in data["repos"].items():
            entries.append(MaintenanceEntry(repo_id=repo_id, **details))

        maintenance = cls(
            last_updated=data["last_updated"],
            last_updated_by=data["last_updated_by"],
            entries=tuple(entries),
        )

        return maintenance

    def _export_dict(self):
        """export a raw dictionary of maintenance report"""
        report = {
            "last_updated": self.last_updated,
            "last_updated_by": self.last_updated_by,
            "repos": {},
        }

        for entry in self.entries:
            report["repos"].update(
                {
                    entry.repo_id: {
                        "message": entry.message,
                        "owner": entry.owner,
                        "started": entry.started,
                    }
                }
            )

        return report

    def add(self, repo_ids, **kwargs):
        """Add entries to maintenance report and update the timestamp. Every
        entry added to the report represents a repository in maintenace mode.

        Args:
            repo_ids (list[str]):
                A list of repository ids. New entries with these repository ids will
                be added to the maintenance report.

                Note: it's users' responsibility to make sure the repository exists in
                the pulp server, this method doesn't check the repository's existence.

            Optional keyword args:
                message (str):
                    Reason why put the repo to maintenance.
                owner (str):
                    Who set the maintenance mode.
        Returns:
           :class:`~pubtools.pulplib.MaintenanceReport`
                A copy of this maintenance report with added repositories.

        """
        message = kwargs.get("message") or "Maintenance mode is enabled"
        owner = kwargs.get("owner") or self._OWNER

        to_add = []
        for repo in repo_ids:
            to_add.append(MaintenanceEntry(repo_id=repo, owner=owner, message=message))
        entries = list(self.entries)
        entries.extend(to_add)

        # filter out duplicated entries. Filtering is in reverse order, which
        # means existed entries will be replaced by newer ones with same repo_id
        filtered_entries = []
        entry_ids = set()
        for entry in reversed(entries):
            if entry.repo_id not in entry_ids:
                filtered_entries.append(entry)
                entry_ids.add(entry.repo_id)

        return attr.evolve(
            self,
            entries=tuple(filtered_entries),
            last_updated_by=owner,
            last_updated=iso_time_now(),
        )

    def remove(self, repo_ids, **kwargs):
        """Remove entries from the maintenance report. Remove entries means the
        removing corresponding repositories from maintenance mode.

        Args:
            repo_ids (list[str]):
                A list of repository ids. Entries match repository ids will be removed
                from the maintenance report.

            Optional keyword args:
                owner (str):
                    Who unset the maintenance mode.
        Returns:
            :class:`~pubtools.pulplib.MaintenanceReport`
                A copy of this maintenance report with removed repositories.
        """
        owner = kwargs.get("owner") or self._OWNER

        repo_ids = set(repo_ids)
        # convert to set, make checking faster
        new_entries = []
        for entry in self.entries:
            if entry.repo_id not in repo_ids:
                new_entries.append(entry)

        return attr.evolve(self, last_updated_by=owner, entries=tuple(new_entries))