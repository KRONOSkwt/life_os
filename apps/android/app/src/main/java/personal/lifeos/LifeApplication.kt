package personal.lifeos

import android.app.Application
import android.content.Context
import androidx.work.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit

class LifeApplication : Application() {
    val repository by lazy { Repository(LifeDatabase.open(this)) }
    val vault by lazy { CredentialVault(this) }
    override fun onCreate() {
        super.onCreate()
        WorkManager.getInstance(this).enqueueUniquePeriodicWork("life-os-sync", ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
    }
    fun scheduleSync() {
        WorkManager.getInstance(this).enqueueUniqueWork("life-os-sync-now", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
    }
}

class SyncWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val app = applicationContext as LifeApplication
        try {
            val credentials = app.vault.read() ?: return@withContext Result.success()
            app.repository.sync(ServerApi(credentials))
            app.repository.dao.put(Metadata("sync_status", syncCompletionMessage(
                app.repository.dao.pending(), "Sincronizado: ${java.time.Instant.now()}")))
            Result.success()
        } catch (error: Exception) {
            if (error is kotlinx.coroutines.CancellationException) throw error
            app.repository.dao.put(Metadata("sync_status", syncError(error)))
            if (error is AccessRejected || error is javax.net.ssl.SSLException || error is IllegalArgumentException)
                Result.failure() else Result.retry()
        }
    }
}

fun syncError(error: Exception): String = when (error) {
    is AccessRejected -> error.message!!
    is javax.net.ssl.SSLException -> "No se pudo verificar el certificado o la dirección. Revisa la invitación del PC."
    is java.io.IOException -> "Sin conexión con el PC. Tus sesiones y cambios pendientes están guardados."
    is IllegalArgumentException -> error.message ?: "Datos inválidos"
    else -> "No se pudo completar la operación. Los datos locales se conservan."
}
