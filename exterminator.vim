
function! StartDebugger(...)
    Rooter
    if len(v:servername) == 0
        echo "Debugging only works with vim started in server mode!"
    endif
    exec 'silent ! tmux split -p 30 "VIM_DEBUG_SERVER=' . v:servername . ' ' . g:exterminator_dir . '/dbg '. join(a:000, ' ') . '"'
endfunction

python << EOF

import vim
import sys, os, psutil, subprocess, threading
sys.path.append(os.path.expanduser(vim.eval("g:exterminator_dir")))
import gdb_fancy

def InitRemoteGdb():
    vim.gdb = gdb_fancy.RemoteGdb(vim)

def get_loc():
    return vim.current.buffer.name, vim.current.range.start + 1

def get_tok():
    return vim.eval('expand("<cword>")')

def display(data, window_name='mandrews', new_command='bot 15new'):
    winnr = int(vim.eval("winnr()"))
    try:
        while True:
            vim.command("wincmd w")
            if int(vim.eval("exists('b:mandrews_output_window')")) > 0:
                if str(vim.eval("b:mandrews_output_window")) == window_name:
                    break
            if winnr == int(vim.eval("winnr()")):
                vim.command(new_command)
                vim.command("setlocal buftype=nowrite bufhidden=wipe modifiable nobuflisted noswapfile nowrap nonumber")
                vim.command("let b:mandrews_output_window='%s'" % window_name)
                break
        vim.current.window.buffer[:] = data.split('\n')
        vim.command("setlocal nomodifiable")
    finally:
        vim.command("%swincmd w" % winnr)

EOF

comm! -nargs=0 GdbToggle   python vim.gdb.toggle_break(*get_loc())
comm! -nargs=0 GdbContinue python vim.gdb.send_continue()
comm! -nargs=0 GdbNext     python vim.gdb.send_next()
comm! -nargs=0 GdbStep     python vim.gdb.send_step()
comm! -nargs=0 GdbEval     python display("%s:\n%s" % (get_tok(), vim.gdb.eval_expr(get_tok())))
comm! -nargs=+ Dbg         call StartDebugger(<f-args>)

highlight SignColumn guibg=Black guifg=White ctermbg=None ctermfg=White

sign define breakpoint text=>> texthl=Comment
sign define just_pc text=-- texthl=Debug
sign define pc_and_breakpoint text=-> texthl=Debug

