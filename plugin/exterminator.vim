let g:exterminator_dir = expand("<sfile>:p:h:h")

python << EOF

import vim
import sys, os, json
sys.path.insert(0, os.path.join(vim.eval("g:exterminator_dir"), 'lib'))
import vim_exterminator

vim.gdb = None

def InitRemoteGdb(host_port=None):
    if host_port is None:
        try:
            exterminator_file = vim.eval('g:exterminator_file')
            host, port = json.loads(open(exterminator_file, 'r').read())
        except Exception as e:
            vim.command("echoerr 'Problem encountered initializing GDB from file %s: %s'" % (exterminator_file, str(e)))
        vim.command("unlet g:exterminator_file")
    else:
        try:
            host, port = host_port.split(':')
            host = '127.0.0.1' if not host else host
            port = int(port)
        except:
            vim.command("echoerr 'Could not parse integer: %s'" % port)
    try:
        vim.gdb = vim_exterminator.RemoteGdb(vim, host, port)
        vim.gdb.set_tmux_pane()
        vim.gdb.handle_events()
    except:
        vim.command("echoerr 'Could not connect to %s:%d'" % (host, port))

EOF

function! GdbIsAttached()
    return pyeval("int(vim.gdb is not None)")
endfunction

function! StartDebugger(...)
    let g:exterminator_file = substitute(system('mktemp'), '\n$', '', '')
    let exe = join(a:000, ' ')
    let exterminate = g:exterminator_dir . '/bin/exterminate'
    call system('tmux split -d -p 30 -h "EXTERMINATOR_FILE=' . g:exterminator_file . ' ' . exterminate . ' ' . exe . '"')
endfunction

function! HistPreserve(cmd)
    call histdel("cmd", -1)
    echo ""
    exec a:cmd
endfunction

let s:Plugin = {}
function! s:Plugin.FetchChildren(str)
    let ret = pyeval('vim.gdb.fetch_children(vim.eval("a:str"))')
    return ret
endfunction

let g:NERDTreeGDBPlugin = s:Plugin

comm! -nargs=1                      GdbExec                 python vim.gdb is None or vim.gdb.send_exec(<f-args>)
comm! -nargs=1                      GdbEval                 python vim.gdb is None or vim.gdb.print_expr(<f-args>)
comm! -nargs=0                      GdbLocals               python vim.gdb is None or vim.gdb.track_expr('auto')
comm! -nargs=0                      GdbNoTrack              python vim.gdb is None or vim.gdb.track_expr(None)
comm! -nargs=0                      GdbBacktrace            python vim.gdb is None or vim.gdb.show_backtrace()

comm! -nargs=0                      GdbContinue             python vim.gdb is None or vim.gdb.send_continue()
comm! -nargs=0                      GdbToggle               python vim.gdb is None or vim.gdb.toggle_break(vim.eval("expand('%:p')"), int(vim.eval("line('.')")))
comm! -nargs=0                      GdbNext                 GdbExec next
comm! -nargs=0                      GdbStep                 GdbExec step
comm! -nargs=0                      GdbUntil                python vim.gdb is None or vim.gdb.continue_until(vim.eval("expand('%:p')"), int(vim.eval("line('.')")))
comm! -nargs=0                      GdbQuit                 python vim.gdb is None or vim.gdb.quit()
comm! -nargs=0                      GdbBindBufferToFrame    nnoremap <buffer> <cr> :exec "GdbExec f " . string(line(".") - 1)<cr><cr>

comm! -nargs=0                      GdbRefresh              python vim.gdb is None or vim.gdb.handle_events()
comm! -nargs=?                      GdbConnect              python InitRemoteGdb(<f-args>)

comm! -nargs=+ -complete=shellcmd   GdbStartDebugger        call StartDebugger(<f-args>)
comm! -nargs=+ -complete=shellcmd   Dbg                     call StartDebugger('-ex r', '--args', <f-args>)

highlight SignColumn guibg=Black guifg=White ctermbg=None ctermfg=White

sign define breakpoint text=>> texthl=Comment
sign define just_pc text=-- texthl=Debug
sign define pc_and_breakpoint text=-> texthl=Debug
sign define dummy

au CursorHold *             GdbRefresh

let g:NERDTreeSortOrder = [ '*' ]
