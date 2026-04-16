# github-spy
Monitor all publicly available event information for a GitHub account.

## Usage
```sh
chmod +x ./gh_user_monitor.py
export GH_TOKEN='YOUR_TOKEN_HERE'

# one-shot
./gh_user_monitor.py snapshot torvalds --mode all --state-dir ./state-torvalds

# poll every 15 minutes
./gh_user_monitor.py watch torvalds --mode all --interval 900 --state-dir ./state-torvalds
```
