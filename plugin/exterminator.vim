let g:exterminator_dir = expand("<sfile>:p:h:h")

python << EOF

import vim
import sys, os, json
sys.path.insert(0, os.path.join(vim.eval("g:exterminator_dir"), 'lib'))
import vim_exterminator

vim.gdb = None

def InitRemoteGdb():
    try:
        host, port = json.loads(open(vim.eval('g:exterminator_file'), 'r').read())
        vim.gdb = vim_exterminator.RemoteGdb(vim, host, port)
        vim.command("unlet g:exterminator_file")
    except:
        vim.command("echoerr 'Problem encountered initializing GDB from file ' . g:exterminator_file")

EOF

function! StartDebugger(...)
    let g:exterminator_file = substitute(system('mktemp'), '\n$', '', '')
    let exe = join(a:000, ' ')
    let exterminate = g:exterminator_dir . '/lib/exterminate'
    exec 'silent ! tmux split -d -p 30 -h "EXTERMINATOR_FILE=' . g:exterminator_file . ' ' . exterminate . ' ' . exe . '"'
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
comm! -nargs=1                      GdbEval                 python vim.gdb is None or vim.gdb.track_expr(<f-args>)
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
comm! -nargs=0                      GdbConnect              python InitRemoteGdb()

comm! -nargs=+ -complete=shellcmd   Dbg                     call StartDebugger(<f-args>)

highlight SignColumn guibg=Black guifg=White ctermbg=None ctermfg=White

sign define breakpoint text=>> texthl=Comment
sign define just_pc text=-- texthl=Debug
sign define pc_and_breakpoint text=-> texthl=Debug
sign define dummy

au CursorHold *             GdbRefresh

let g:NERDTreeSortOrder = [ '*' ]
