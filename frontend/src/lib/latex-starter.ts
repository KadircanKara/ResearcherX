/**
 * The seed source for a blank project.
 *
 * The backend defaults a new document's main file to EMPTY on purpose -- a
 * starter template is a client-side choice, not a server default -- so this is
 * passed explicitly to `createDocument`. It lives in `lib/` rather than in a
 * component because both the LaTeX project list and anything that later
 * creates a document need the identical bytes.
 */
export const STARTER = `\\documentclass[conference]{IEEEtran}
\\begin{document}
\\title{Untitled}
\\author{}
\\maketitle

\\section{Introduction}

\\end{document}
`;
