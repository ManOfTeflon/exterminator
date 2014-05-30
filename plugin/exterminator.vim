let g:exterminator_dir = expand("<sfile>:p:h:h")

python << EOF

import vim
import sys, os, json, time
sys.path.insert(0, os.path.join(vim.eval("g:exterminator_dir"), 'lib'))
import exterminator

vim.gdb = None

def InitRemoteGdbWithFile(exterminator_file):
    while True:
       try:
           data = open(exterminator_file, 'r').read()
           host, port = json.loads(open(exterminator_file, 'r').read())
           break
       except ValueError, IOError:
           time.sleep(0.1)
    InitRemoteGdb(host, port)

def InitRemoteGdb(host, port):
    vim.gdb = exterminator.RemoteGdb(vim, host, port)

EOF

function! StartDebugger(...)
    let g:exterminator_file = substitute(system('mktemp'), '\n$', '', '')
    exec 'silent ! tmux split -d -p 30 -h "EXTERMINATOR_FILE=' . g:exterminator_file . ' ' . g:exterminator_dir . '/lib/exterminate '. join(a:000, ' ') . '"'
endfunction

comm! -nargs=0 GdbToggle    python vim.gdb is None or vim.gdb.toggle_break(vim.current.buffer.name, vim.current.range.start + 1)
comm! -nargs=0 GdbContinue  python vim.gdb is None or vim.gdb.send_continue()
comm! -nargs=0 GdbNext      python vim.gdb is None or vim.gdb.send_next()
comm! -nargs=0 GdbStep      python vim.gdb is None or vim.gdb.send_step()
comm! -nargs=1 GdbEval      python vim.gdb is None or vim.gdb.eval_expr(<f-args>)
comm! -nargs=0 GdbEvalToken python vim.gdb is None or vim.gdb.eval_expr(vim.eval("expand('<cword>')"))
comm! -nargs=0 GdbQuit      python vim.gdb is None or vim.gdb.quit()
comm! -nargs=0 GdbRefresh   python vim.gdb is None or vim.gdb.handle_events()
comm! -nargs=+ Dbg          call StartDebugger(<f-args>)

highlight SignColumn guibg=Black guifg=White ctermbg=None ctermfg=White

sign define breakpoint text=>> texthl=Comment
sign define just_pc text=-- texthl=Debug
sign define pc_and_breakpoint text=-> texthl=Debug

au CursorHold *             GdbRefresh
