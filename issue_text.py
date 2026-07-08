ISSUE="""
raise FileNotFoundError for missing TLS material
 #7564
Open
Open
raise FileNotFoundError for missing TLS material
#7564
Description
@JKDingwall
JKDingwall
opened 3h ago
Would you accept a PR which changes the existing OSError for missing TLS material to FileNotFoundError?

e.g.:

            if conn.cert_file and not os.path.exists(conn.cert_file):
                raise OSError(
                    f"Could not find the TLS certificate file, "
                    f"invalid path: {conn.cert_file}"
                )
becomes something like:

            if conn.cert_file and not os.path.exists(conn.cert_file):
                from errno import ENOENT
                raise FileNotFoundError(
                    ENOENT,
                    "Could not find the TLS certificate file, invalid path",
                    str(conn.cert_file)
                )
Our use case is that there are temporary conditions where the files don't exist and it'd be nicer to handle FileNotFoundError specifically and be able to compare the .filename attribute rather than the more general OSError and test str(excp).endswith(...). As FileNotFoundError is a subclass of OSError existing try/excepts continue to work but it would change the string representation:

>>> str(OSError("Could not find the TLS certificate file, invalid path: filename"))
'Could not find the TLS certificate file, invalid path: filename'
>>> str(FileNotFoundError(errno.ENOENT, "Could not find the TLS certificate file, invalid path", "filename"))
"[Errno 2] Could not find the TLS certificate file, invalid path: 'filename'"
Activity

muhamedfazalps
mentioned this 3h ago
fix: raise FileNotFoundError for missing TLS material #7565
sigmavirus24 commented 2 hours ago
@sigmavirus24
sigmavirus24
2h ago
Contributor
If my read of the docs is accurate, this is a subclass of OSError so it's fine. The PR that was created is a spam automation that opened this before anyone agreed to it and they have opened more than 20 on other repos I've seen.

If you'd like to send a PR for this @JKDingwall I'm happy to review this but please ensure you add unit tests for this change and update them.


"""