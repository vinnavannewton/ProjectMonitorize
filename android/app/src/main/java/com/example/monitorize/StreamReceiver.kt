package com.example.monitorize

import android.util.Log
import java.net.ServerSocket

/**
 * Reads raw H.264 Annex B bytes from TCP and feeds them directly to the decoder.
 * No NAL parsing — MediaCodec handles start-code detection internally.
 *
 * This raw-chunk approach produces the best results on Samsung Tab S7 FE
 * (Qualcomm Snapdragon 750G). NAL-aligned and CODEC_CONFIG approaches
 * caused full-frame chroma corruption on this device.
 */
class StreamReceiver(private val decoder: H264Decoder) {

    private var running = false
    private var serverSocket: ServerSocket? = null

    var onStatusChange: ((String) -> Unit)? = null

    companion object {
        private const val TAG = "StreamReceiver"
        private const val PORT = 7110

        // Must match linux/monitorize_fallback.py
        private const val STREAM_WIDTH  = 1280
        private const val STREAM_HEIGHT = 800
        private const val STREAM_FPS    = 60
    }

    fun start() {
        running = true
        Thread(::receiveLoop, "MonitorizeReceiver").start()
    }

    private fun receiveLoop() {
        try {
            serverSocket = ServerSocket(PORT)
            onStatusChange?.invoke("Waiting for connection…")

            val socket = serverSocket!!.accept()
            socket.tcpNoDelay = true
            socket.receiveBufferSize = 512 * 1024
            onStatusChange?.invoke("Connected")

            decoder.init(STREAM_WIDTH, STREAM_HEIGHT, STREAM_FPS)
            onStatusChange?.invoke("Stream: ${STREAM_WIDTH}×${STREAM_HEIGHT} @ ${STREAM_FPS}fps")

            // Read raw TCP bytes and feed directly to MediaCodec.
            // The codec handles Annex B start-code detection internally.
            val buf = ByteArray(256 * 1024)
            val input = socket.getInputStream()

            while (running) {
                val n = input.read(buf)
                if (n <= 0) break
                decoder.feedChunk(buf, 0, n)
            }

        } catch (e: Exception) {
            if (running) {
                Log.e(TAG, "Stream error", e)
                onStatusChange?.invoke("Error: ${e.message}")
            }
        }
    }

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
    }
}
