package org.safechat.guardian.core

import kotlinx.coroutines.CancellationException

/** Offline queue of events waiting for the backend. Implemented by an encrypted Room DB on the
 *  device (`data/RoomEventQueue`) and in memory for tests. */
interface EventQueue {
    suspend fun enqueue(event: WireEvent)
    suspend fun peek(limit: Int): List<WireEvent>
    suspend fun delete(eventIds: Collection<String>)
    suspend fun count(): Int
    suspend fun clear()
}

/** Sends a batch and returns the event IDs the backend acknowledged. */
fun interface EventTransport {
    suspend fun sendAll(events: List<WireEvent>): Set<String>
}

data class FlushResult(val sent: Int, val remaining: Int, val stalled: Boolean)

/** Drains the queue: send a batch, delete exactly what was acknowledged, repeat. Stops when the
 *  queue is empty or a round makes no progress (offline / server busy) so WorkManager can retry. */
class QueueFlusher(
    private val queue: EventQueue,
    private val transport: EventTransport,
    private val batchSize: Int = 100,
    private val maxRounds: Int = 50,
) {
    suspend fun flush(): FlushResult {
        var sent = 0
        var stalled = false
        repeat(maxRounds) {
            val batch = queue.peek(batchSize)
            if (batch.isEmpty()) return FlushResult(sent, 0, false)
            val acked = try {
                transport.sendAll(batch)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                emptySet()
            }
            val ids = batch.map { it.eventId }.filter { it in acked }
            if (ids.isNotEmpty()) queue.delete(ids)
            sent += ids.size
            if (ids.isEmpty()) {
                stalled = true
                return FlushResult(sent, queue.count(), stalled)
            }
        }
        return FlushResult(sent, queue.count(), stalled)
    }
}

class InMemoryEventQueue(private val maxSize: Int = 5000) : EventQueue {
    private val lock = Any()
    private val items = LinkedHashMap<String, WireEvent>()

    override suspend fun enqueue(event: WireEvent) = synchronized(lock) {
        items.putIfAbsent(event.eventId, event)
        while (items.size > maxSize) items.remove(items.keys.first())
    }

    override suspend fun peek(limit: Int): List<WireEvent> = synchronized(lock) { items.values.take(limit) }

    override suspend fun delete(eventIds: Collection<String>) = synchronized(lock) {
        eventIds.forEach { items.remove(it) }
    }

    override suspend fun count(): Int = synchronized(lock) { items.size }

    override suspend fun clear() = synchronized(lock) { items.clear() }
}
