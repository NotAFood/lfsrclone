# git-lfs rclone based custom transfer agent

****
# Status

I am not actively developing this tool anymore. There wasn't enough interest, including for my own use cases, to warrant them time. 

With that said, here is the original roadmap — items completed in this fork are marked:

- Change flag API to not use the deprecated and undocumented `argparse` feature. The current approach is nice but I don't like relying on undocumented features. Instead, there will be a flag where you then specify rclone options. (minor)
- [x] Move to the rclone API to allow sessions greatly reducing traffic, API Calls, costs, etc. (major) — **done**: uses `rclone rcd` with `operations/copyfile` via HTTP; one daemon per session

***

# BETA

This software is still beta. A *non-exhaustive* and only *roughly* ordered list of priorities are:

- [ ] Test (and fix?) on windows
- [ ] Improve tests to handle/verify other edge cases
    - subdirectories (work but not tested)
    - conflicting rclone flags
    - test for line coverage
    - committed rclone config (similar chicken-and-egg as noted before)
    - tests for additional error capture from rclone
- [ ] Document migrations (if possible)
- [x] Better distribution and `pyproject.toml` format — **done**
- [x] type annotations — **done**
- [x] Better parallel working — **done**: see Concurrency and Transfers below
- [ ] real-world production testing


***
***


Implements a pretty simple [custom-transfer agent][cta] for [git-lfs][lfs].

[cta]:https://github.com/git-lfs/git-lfs/blob/main/docs/custom-transfers.md
[lfs]:https://git-lfs.github.com/

This is **BETA**. See Known Issues and Roadmap for more details.

This project is heavily inspired by [lfs-folderstore][folder] and [git-lfs-swift-transfer-agent][swift] (The idea mostly came from the former but the latter, being that it is in Python, was useful). [git-lfs-rsync-agent][rsync] also proved to be valuable in development.

[folder]:https://github.com/sinbad/lfs-folderstore
[swift]:https://github.com/cbartz/git-lfs-swift-transfer-agent
[rsync]:https://github.com/aleb/git-lfs-rsync-agent

## Install

**PyPI To come later**


    $ python -m pip install git+https://github.com/Jwink3101/lfsrclone

## Configure LFS

TODO: INSTALL

The following are optional flags that can be specified below:

```
usage: lfsrclone [-h] [--log-file LOG_FILE]
                  [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL,NONE}]
                  [--rclone-exe RCLONE_EXE] [--temp-dir TEMP_DIR]
                  [--rc-stats-interval RC_STATS_INTERVAL]
                  remote

positional arguments:
  remote                Specify rclone remote

optional arguments:
  -h, --help            show this help message and exit
  --log-file LOG_FILE   [.git/lfsrclone.log] Specify alternative log file
                        destination
  --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL,NONE}
                        Logging levels. Set to None to (effectivly) disable
                        logging (i.e. set to 9999)
  --rclone-exe RCLONE_EXE
                        ['rclone'] Specify rclone executable.
  --temp-dir TEMP_DIR   [.git/lfsrclone-tmp] Specify a temporary download
                        directory
  --rc-stats-interval RC_STATS_INTERVAL
                        [0.1] Polling interval in seconds for RC API progress
                        updates

All additional arguments are passed to rclone rcd
```




This section is based on a similar one from [lfs-folderstore][folder].

Download and install [git-lfs][lfs]. Make sure you have followed the directions to begin such as `git lfs install` (globally) and `git lfs track *.ext` (locally), and `git add .gitattributes`

### Starting a new repo

This assumes you have already set up LFS as noted above.

Set the following:

    $ git config --add lfs.customtransfer.lfsrclone.path lfsrclone

Or, if you did not install lfsrclone, you can specify the full path to the Python file

Then,

    $ git config --add lfs.standalonetransferagent lfsrclone
    
And finally,

    $ git config --add lfs.customtransfer.lfsrclone.args "remote: <lfsrclone-options> <additional rclone flags>"
    
Note in the above that all arguments must be escaped properly so it is passed to `git-config` as just one. Alternatively, do something like:

    $ git config --add lfs.customtransfer.lfsrclone.args TMP

then open `.git/config` and you will see lines like:

```ini
[lfs "customtransfer.lfsrclone"]
	path = lfsrclone
	args = TMP
```
You can then set `TMP` to your full rclone command including `\` for line continuation, etc. `<lfsrclone-options>` are those noted above including the log file.

### Cloning an existing repo

Cloning an existing lfsrclone repo presents a "chicken and the egg" problem with how to configure.

To do this, you tell git-lfs not to download files.

    $ export GIT_LFS_SKIP_SMUDGE=1
    $ git clone <repo>
    $ unset GIT_LFS_SKIP_SMUDGE

    ##### OR #####
    
    $ GIT_LFS_SKIP_SMUDGE=1 git clone <repo>
    
(this assumes Bash but it is similar for other shells)
    
Then move into the repo and set up as per above:

    $ git config --add lfs.customtransfer.lfsrclone.path lfsrclone
    $ git config --add lfs.standalonetransferagent lfsrclone
    $ git config --add lfs.customtransfer.lfsrclone.args "remote: <flags>" # or TMP and replace
    
Finally:

    $ git lfs pull 

### Additional Notes

Since lfsrclone does not support file-locking, you may have to set

    $ git config lfs.locksverify false

inside the repo

## Concurrency and Transfers

The [custom transfer protocol][cta] is serial per process: git-lfs sends one
transfer event and waits for the `complete` response before sending the next.
Parallelism is achieved by git-lfs spawning **multiple lfsrclone processes
simultaneously**, each handling its own serial stream of transfers.

The right knob is [`lfs.concurrenttransfers`][concurrent] (default 8), which
controls how many lfsrclone processes git-lfs runs at once. Each process starts
one `rclone rcd` daemon at init time and reuses it for all its transfers, so
startup cost is paid once per session rather than once per file.

rclone's `--transfers` flag has no effect here: each daemon handles one file
at a time by protocol, and splitting transfers across multiple daemons is
already what `lfs.concurrenttransfers` does. Don't set `--transfers` expecting
it to improve throughput.

To cap or expand parallelism, set:

    $ git config lfs.concurrenttransfers 4

[concurrent]:https://github.com/git-lfs/git-lfs/blob/main/docs/man/git-lfs-config.5.ronn#upload-and-download-transfer-settings
[custom concurrent]: https://github.com/git-lfs/git-lfs/blob/main/docs/custom-transfers.md#defining-a-custom-transfer-type

## Rclone Flags

Additional arguments are passed directly to `rclone rcd` at startup and apply for the
entire session. lfsrclone sets the following automatically on the daemon:

- [`--rc-no-auth`][rc]: no authentication required (loopback only)
- [`--ask-password=false`][ap]: no password prompts

Per-transfer options that previously had to be passed as rclone flags are now handled
internally via the RC API `_config` parameter:

- `SizeOnly: true` (equivalent to `--size-only`) — skip ModTime checks

[rc]:https://rclone.org/rc/
[ap]:https://rclone.org/docs/#configuration-encryption

### Tips

Progress granularity is controlled by `--rc-stats-interval` (default 0.1 s). Pass
`--rc-stats-interval 0.05` for finer updates.

### Incompatible Flags

Do not pass the following — lfsrclone sets them internally:

- `--rc-addr`, `--rc-no-auth` — managed by lfsrclone

## Known Limitations

* Cannot perform locking. See [#4314](https://github.com/git-lfs/git-lfs/issues/4314#issuecomment-730434427)

## Contributing

Format and lint with `ruff format` and `ruff check` before committing.

## Background

I have always been disappointed that git-lfs required a special server. I don't care that it breaks the decentralized nature (though it does!) but it means that hosting requires more than just a simple ssh server. And portability becomes harders.

I've considered [git-fat][fat] but it is outdated. I've looked at [git-annex][annex] but it is (a) super (!!!) complicated, (b) not widely used, and (c) I don't like the symlink approach (even though smudge-filter approach also has its issues).

I've considered updating git-fat but decided it wasn't worth it. I ended up writing my own tool fully but found the edge-cases and testing to be more than I was willing to do (though it was a good learning experience!). So I left it alone.

But when I found [lfs-folderstore][folder], I learned about custom transfer agents. Suddenly, it became possible to let git-lfs handle the edge cases and the user interface and just let me handle the data! Win-win!

[fat]:https://github.com/jedbrown/git-fat
[annex]:https://git-annex.branchable.com/



