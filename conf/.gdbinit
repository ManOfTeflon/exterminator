set verbose off
set history filename /tmp/exterminator/conf/.gdb_history
set history save

# These make gdb never pause in its output
set height 0
set width 0
set pagination off

define exterminate
    source /tmp/exterminator/lib/exterminator.py
end

set print thread-events off
set print pretty on
