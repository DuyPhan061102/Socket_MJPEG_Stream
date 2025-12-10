class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		try:
			self.file = open(filename, 'rb')
		except:
			raise IOError
		self.frameNum = 0
		
	def nextFrame(self):
		"""Get next frame."""
		header = self.file.read(5)
		if header and len(header) == 5:
			try:
				# Try ASCII format first (original movie.Mjpeg format)
				framelength = int(header.decode('ascii').strip())
			except:
				# Fallback to binary format (big-endian)
				framelength = int.from_bytes(header, byteorder='big')
			
			# Validate frame length (sanity check)
			if 0 < framelength < 10000000:  # Max 10MB per frame
				# Read the actual frame data
				data = self.file.read(framelength)
				if len(data) == framelength:
					self.frameNum += 1
					return data
		return None
		
	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum