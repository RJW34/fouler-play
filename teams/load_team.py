import random
import os
from .team_converter import export_to_packed, export_to_dict

TEAM_DIR = os.path.dirname(os.path.abspath(__file__))


class TeamListIterator:
    _INDEX_FILE = os.path.join(TEAM_DIR, ".team_iterator_index")

    def __init__(self, team_list_file_or_names):
        # Support both file path (str) and direct list of team names
        if isinstance(team_list_file_or_names, list):
            self.team_names = team_list_file_or_names
        else:
            with open(os.path.join(TEAM_DIR, team_list_file_or_names), "r") as f:
                lines = f.readlines()
            self.team_names = [line.strip() for line in lines if line.strip()]
        # Restore persisted index so rotation survives restarts
        self.index = self._load_index()

    def _load_index(self):
        try:
            with open(self._INDEX_FILE, "r") as f:
                idx = int(f.read().strip())
                if 0 <= idx < len(self.team_names):
                    return idx
        except (FileNotFoundError, ValueError):
            pass
        return 0

    def _save_index(self):
        try:
            with open(self._INDEX_FILE, "w") as f:
                f.write(str(self.index))
        except OSError:
            pass

    def get_next_team(self):
        if not self.team_names:
            raise ValueError("Team list is empty")
        old_idx = self.index
        team_name = self.team_names[self.index]
        self.index = (self.index + 1) % len(self.team_names)
        self._save_index()
        import logging
        logging.getLogger(__name__).info(
            f"TeamListIterator(id={id(self)}): idx {old_idx}->{self.index}, "
            f"teams={len(self.team_names)}, returning={team_name}"
        )
        return team_name


def load_team(name):
    if name is None:
        return "null", "", ""

    path = os.path.join(TEAM_DIR, "{}".format(name))
    if os.path.isdir(path):
        team_file_names = list()
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.startswith("."):
                    continue
                full_path = os.path.join(root, f)
                if os.path.isfile(full_path):
                    team_file_names.append(full_path)
        if not team_file_names:
            raise ValueError("No team files found in dir: {}".format(name))
        file_path = random.choice(team_file_names)

    elif os.path.isfile(path):
        file_path = path
    else:
        raise ValueError("Path must be file or dir: {}".format(name))

    with open(file_path, "r") as f:
        team_export = f.read()

    return (
        export_to_packed(team_export),
        export_to_dict(team_export),
        os.path.basename(file_path),
    )
