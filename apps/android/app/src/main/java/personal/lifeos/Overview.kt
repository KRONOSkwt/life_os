package personal.lifeos

fun registeredSessionCount(records: List<Central>, drafts: List<Draft>): Int =
    (records.filter { it.type == "session" && !it.deleted }.map { it.id } +
        drafts.filter { it.completed }.map { it.id }).toSet().size

fun syncCompletionMessage(pending: List<Pending>, success: String = "Sincronización completada"): String =
    if (pending.any { it.conflict != null }) "Revisa las sesiones en conflicto. Tu copia local está guardada."
    else success
