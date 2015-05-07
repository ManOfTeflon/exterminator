set verbose off
set history filename /tmp/exterminator/.gdb_history
set history save

# These make gdb never pause in its output
set height 0
set width 0
set pagination off

define exterminate
    source /tmp/exterminator/exterminator.py
end

set print thread-events off
set print pretty on
