from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
import queue
import time

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

class Client:
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT
	
	SETUP = 0
	PLAY = 1
	PAUSE = 2
	TEARDOWN = 3
	
	def __init__(self, master, serveraddr, serverport, rtpport, filename):
		self.master = master
		self.createWidgets()
		self.serverAddr = serveraddr
		self.serverPort = int(serverport)
		self.rtpPort = int(rtpport)
		self.fileName = filename
		self.rtspSeq = 0
		self.sessionId = 0
		self.requestSent = -1
		self.teardownAcked = 0
		self.connectToServer()
		self.frameNbr = 0


		# Client-Side Caching
		self.frameBuffer = queue.Queue()
		self.BUFFER_THRESHOLD = 20
		self.is_buffering = True
		
		# Statistics tracking
		self.stats = {
			'total_packets': 0,
			'total_bytes': 0,
			'expected_seq': 1,
			'lost_packets': 0,
			'start_time': None
		}
		
		# Setup window close handler (must be at the end)
		self.master.protocol("WM_DELETE_WINDOW", self.handler)

		
	def createWidgets(self):
		"""Build GUI."""
		# Create Setup button
		self.setup = Button(self.master, width=20, padx=3, pady=3)
		self.setup["text"] = "Setup"
		self.setup["command"] = self.setupMovie
		self.setup.grid(row=1, column=0, padx=2, pady=2)
		
		# Create Play button		
		self.start = Button(self.master, width=20, padx=3, pady=3)
		self.start["text"] = "Play"
		self.start["command"] = self.playMovie
		self.start.grid(row=1, column=1, padx=2, pady=2)
		
		# Create Pause button			
		self.pause = Button(self.master, width=20, padx=3, pady=3)
		self.pause["text"] = "Pause"
		self.pause["command"] = self.pauseMovie
		self.pause.grid(row=1, column=2, padx=2, pady=2)
		
		# Create Teardown button
		self.teardown = Button(self.master, width=20, padx=3, pady=3)
		self.teardown["text"] = "Teardown"
		self.teardown["command"] = self.exitClient
		self.teardown.grid(row=1, column=3, padx=2, pady=2)
		
		# Create a label to display the movie
		self.label = Label(self.master, height=19)
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)
		
		# Statistics display
		self.statsLabel = Label(self.master, text="Statistics: Waiting...", 
		                        font=("Arial", 10), anchor=W, justify=LEFT)
		self.statsLabel.grid(row=2, column=0, columnspan=4, sticky=W+E, padx=5, pady=5)
	
	def setupMovie(self):
		"""Setup button handler."""
		if self.state == self.INIT:
			self.sendRtspRequest(self.SETUP)
	
	def exitClient(self):
		"""Teardown button handler."""
		self.printFinalStats()
		self.sendRtspRequest(self.TEARDOWN)
		self.master.destroy()
		cache_file = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		if os.path.exists(cache_file):
			os.remove(cache_file)

	def pauseMovie(self):
		"""Pause button handler."""
		if self.state == self.PLAYING:
			self.sendRtspRequest(self.PAUSE)
	
	def playMovie(self):
		"""Play button handler."""
		if self.state == self.READY:
			if self.stats['start_time'] is None:
				self.stats['start_time'] = time.time()
			
			threading.Thread(target=self.recvRtp).start()
			self.playEvent = threading.Event()
			self.playEvent.clear()
			self.sendRtspRequest(self.PLAY)
			self.is_buffering = True 
			self.consumeBuffer()
			self.updateStats()
	
	def recvRtp(self):
		"""Receive RTP packets from server."""
		current_buffer = b""
		while True:
			try:
				data = self.rtpSocket.recv(20480)
				print(f"Received RTP packet (len= {len(data)})")
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					

					# Track statistics
					self.stats['total_packets'] += 1
					self.stats['total_bytes'] += len(data)
					
					# Detect packet loss
					current_seq = rtpPacket.seqNum()
					if current_seq > self.stats['expected_seq']:
						lost = current_seq - self.stats['expected_seq']
						self.stats['lost_packets'] += lost
					self.stats['expected_seq'] = current_seq + 1
					
					# Reassemble fragments
					current_buffer += rtpPacket.getPayload()

					# Display when Marker = 1 (end of frame)
					if rtpPacket.getMarker() == 1:
						if rtpPacket.seqNum() > self.frameNbr:
							self.frameNbr = rtpPacket.seqNum()
							self.frameBuffer.put(current_buffer)

							print(f"Seq: {rtpPacket.seqNum()}, Marker = {rtpPacket.getMarker()}")
							print("Frame added to buffer")
						current_buffer = b""
			except Exception as e:

				if self.playEvent.isSet(): 
					break
				if self.teardownAcked == 1:
					self.rtpSocket.shutdown(socket.SHUT_RDWR)
					self.rtpSocket.close()
					break


	def updateStats(self):
		"""Update statistics display periodically"""
		if self.state == self.PLAYING or self.stats['start_time']:
			elapsed = time.time() - self.stats['start_time'] if self.stats['start_time'] else 0
			bitrate = (self.stats['total_bytes'] * 8 / elapsed / 1000) if elapsed > 0 else 0
			loss_rate = (self.stats['lost_packets'] / max(self.stats['total_packets'], 1)) * 100
			
			stats_text = (
				f"Statistics:\n"
				f"Total Packets: {self.stats['total_packets']} | "
				f"Lost: {self.stats['lost_packets']} ({loss_rate:.2f}%)\n"
				f"Data Received: {self.stats['total_bytes'] / 1024:.2f} KB | "
				f"Bitrate: {bitrate:.2f} kbps | "
				f"Buffer: {self.frameBuffer.qsize()}"
			)
			self.statsLabel.config(text=stats_text)
			self.master.after(500, self.updateStats)
	
	def printFinalStats(self):
		"""Print final statistics to console"""
		elapsed = time.time() - self.stats['start_time'] if self.stats['start_time'] else 0
		print("\n" + "="*50)
		print("FINAL STATISTICS")
		print("="*50)
		print(f"Total Packets Received: {self.stats['total_packets']}")
		print(f"Total Packets Lost: {self.stats['lost_packets']}")
		print(f"Packet Loss Rate: {(self.stats['lost_packets'] / max(self.stats['total_packets'], 1)) * 100:.2f}%")
		print(f"Total Data Received: {self.stats['total_bytes'] / 1024:.2f} KB")
		print(f"Session Duration: {elapsed:.2f} seconds")
		print(f"Average Bitrate: {(self.stats['total_bytes'] * 8 / elapsed / 1000) if elapsed > 0 else 0:.2f} kbps")
		print("="*50 + "\n")


	def writeFrame(self, data):
		"""Write the received frame to a temp image file."""
		cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		file = open(cachename, "wb")
		file.write(data)
		file.close()
		return cachename
	
	def updateMovie(self, imageFile):
		"""Update the image file as video frame in the GUI."""
		photo = ImageTk.PhotoImage(Image.open(imageFile))
		self.label.configure(image=photo, height=288) 
		self.label.image = photo
		
	def connectToServer(self):
		"""Connect to the Server. Start a new RTSP/TCP session."""
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
		except:
			tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' % self.serverAddr)
	
	def sendRtspRequest(self, requestCode):
		"""Send RTSP request to the server."""
		if requestCode == self.SETUP and self.state == self.INIT:
			threading.Thread(target=self.recvRtspReply).start()

			self.rtspSeq += 1
			request = 'SETUP ' + self.fileName + ' RTSP/1.0\nCSeq: ' + str(self.rtspSeq) + '\nTransport: RTP/UDP; client_port= ' + str(self.rtpPort) + '\n'
			self.requestSent = self.SETUP
			
		elif requestCode == self.PLAY and self.state == self.READY:
			self.rtspSeq += 1
			request = 'PLAY ' + self.fileName + ' RTSP/1.0\nCSeq: ' + str(self.rtspSeq) + '\nSession: ' + str(self.sessionId) + '\n'
			self.requestSent = self.PLAY
			
		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			self.rtspSeq += 1
			request = 'PAUSE ' + self.fileName + ' RTSP/1.0\nCSeq: ' + str(self.rtspSeq) + '\nSession: ' + str(self.sessionId) + '\n'
			self.requestSent = self.PAUSE
			
		elif requestCode == self.TEARDOWN and not self.state == self.INIT:
			self.rtspSeq += 1
			request = 'TEARDOWN ' + self.fileName + ' RTSP/1.0\nCSeq: ' + str(self.rtspSeq) + '\nSession: ' + str(self.sessionId) + '\n'
			self.requestSent = self.TEARDOWN
		else:
			return
		
		self.rtspSocket.send(request.encode())
		print('\nData sent:\n' + request)
	
	def recvRtspReply(self):
		"""Receive RTSP reply from the server."""
		while True:
			reply = self.rtspSocket.recv(1024)
			
			if reply: 
				self.processRtspReply(reply.decode("utf-8"))
			
			if self.requestSent == self.TEARDOWN:
				self.rtspSocket.shutdown(socket.SHUT_RDWR)
				self.rtspSocket.close()
				break
	
	def processRtspReply(self, data):
		"""Parse the RTSP reply from the server."""
		lines = data.split('\n')
		seq = lines[1].split(' ')[1]
		
		if int(seq) == self.rtspSeq:
			session = int(lines[2].split(' ')[1])
			if self.sessionId == 0:
				self.sessionId = session
			
			if self.sessionId == session:
				if int(lines[0].split(' ')[1]) == 200: 
					if self.requestSent == self.SETUP:
						self.state = self.READY
						self.openRtpPort() 
					elif self.requestSent == self.PLAY:
						self.state = self.PLAYING
					elif self.requestSent == self.PAUSE:
						self.state = self.READY
						self.playEvent.set()
					elif self.requestSent == self.TEARDOWN:
						self.state = self.INIT
						self.teardownAcked = 1 
	
	def openRtpPort(self):
		"""Open RTP socket binded to a specified port."""
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		self.rtpSocket.settimeout(0.5)
		
		try:
			self.state = self.READY
			self.rtpSocket.bind(('', self.rtpPort))
		except:
			tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' % self.rtpPort)

	def handler(self):
		"""Handler on explicitly closing the GUI window."""
		self.pauseMovie()
		if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()

		else:
			self.playMovie()
			
	def consumeBuffer(self):
		"""Consume frames from buffer and display"""
		try:
			if self.state == self.PLAYING or (self.state == self.READY and self.is_buffering):
				if self.is_buffering:
					if self.frameBuffer.qsize() < self.BUFFER_THRESHOLD:
						self.master.after(20, self.consumeBuffer)
						return
					else:
						self.is_buffering = False
				
				if not self.frameBuffer.empty():
					frameData = self.frameBuffer.get()
					self.updateMovie(self.writeFrame(frameData))
					delay = 40 if self.frameBuffer.qsize() > 50 else 50
					self.master.after(delay, self.consumeBuffer)
				else:
					self.is_buffering = True
					self.master.after(20, self.consumeBuffer)
		except Exception as e:
			print(f"Error in consumeBuffer: {e}")

