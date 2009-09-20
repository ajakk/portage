# Copyright 1999-2009 Gentoo Foundation
# Distributed under the terms of the GNU General Public License v2
# $Id$

from portage.util import writemsg
from portage.data import secpass
import portage
from portage import os

try:
	import cPickle as pickle
except ImportError:
	import pickle

class BlockerCache(portage.cache.mappings.MutableMapping):
	"""This caches blockers of installed packages so that dep_check does not
	have to be done for every single installed package on every invocation of
	emerge.  The cache is invalidated whenever it is detected that something
	has changed that might alter the results of dep_check() calls:
		1) the set of installed packages (including COUNTER) has changed
	"""

	# Number of uncached packages to trigger cache update, since
	# it's wasteful to update it for every vdb change.
	_cache_threshold = 5

	class BlockerData(object):

		__slots__ = ("__weakref__", "atoms", "counter")

		def __init__(self, counter, atoms):
			self.counter = counter
			self.atoms = atoms

	def __init__(self, myroot, vardb):
		self._vardb = vardb
		self._cache_filename = os.path.join(myroot,
			portage.CACHE_PATH, "vdb_blockers.pickle")
		self._cache_version = "1"
		self._cache_data = None
		self._modified = set()
		self._load()

	def _load(self):
		try:
			f = open(self._cache_filename, mode='rb')
			mypickle = pickle.Unpickler(f)
			try:
				mypickle.find_global = None
			except AttributeError:
				# TODO: If py3k, override Unpickler.find_class().
				pass
			self._cache_data = mypickle.load()
			f.close()
			del f
		except (IOError, OSError, EOFError, ValueError, pickle.UnpicklingError) as e:
			if isinstance(e, pickle.UnpicklingError):
				writemsg("!!! Error loading '%s': %s\n" % \
					(self._cache_filename, str(e)), noiselevel=-1)
			del e

		cache_valid = self._cache_data and \
			isinstance(self._cache_data, dict) and \
			self._cache_data.get("version") == self._cache_version and \
			isinstance(self._cache_data.get("blockers"), dict)
		if cache_valid:
			# Validate all the atoms and counters so that
			# corruption is detected as soon as possible.
			invalid_items = set()
			for k, v in self._cache_data["blockers"].iteritems():
				if not isinstance(k, basestring):
					invalid_items.add(k)
					continue
				try:
					if portage.catpkgsplit(k) is None:
						invalid_items.add(k)
						continue
				except portage.exception.InvalidData:
					invalid_items.add(k)
					continue
				if not isinstance(v, tuple) or \
					len(v) != 2:
					invalid_items.add(k)
					continue
				counter, atoms = v
				if not isinstance(counter, (int, long)):
					invalid_items.add(k)
					continue
				if not isinstance(atoms, (list, tuple)):
					invalid_items.add(k)
					continue
				invalid_atom = False
				for atom in atoms:
					if not isinstance(atom, basestring):
						invalid_atom = True
						break
					if atom[:1] != "!" or \
						not portage.isvalidatom(
						atom, allow_blockers=True):
						invalid_atom = True
						break
				if invalid_atom:
					invalid_items.add(k)
					continue

			for k in invalid_items:
				del self._cache_data["blockers"][k]
			if not self._cache_data["blockers"]:
				cache_valid = False

		if not cache_valid:
			self._cache_data = {"version":self._cache_version}
			self._cache_data["blockers"] = {}
		self._modified.clear()

	def flush(self):
		"""If the current user has permission and the internal blocker cache
		been updated, save it to disk and mark it unmodified.  This is called
		by emerge after it has proccessed blockers for all installed packages.
		Currently, the cache is only written if the user has superuser
		privileges (since that's required to obtain a lock), but all users
		have read access and benefit from faster blocker lookups (as long as
		the entire cache is still valid).  The cache is stored as a pickled
		dict object with the following format:

		{
			version : "1",
			"blockers" : {cpv1:(counter,(atom1, atom2...)), cpv2...},
		}
		"""
		if len(self._modified) >= self._cache_threshold and \
			secpass >= 2:
			try:
				f = portage.util.atomic_ofstream(self._cache_filename, mode='wb')
				pickle.dump(self._cache_data, f, protocol=2)
				f.close()
				portage.util.apply_secpass_permissions(
					self._cache_filename, gid=portage.portage_gid, mode=0o644)
			except (IOError, OSError) as e:
				pass
			self._modified.clear()

	def __setitem__(self, cpv, blocker_data):
		"""
		Update the cache and mark it as modified for a future call to
		self.flush().

		@param cpv: Package for which to cache blockers.
		@type cpv: String
		@param blocker_data: An object with counter and atoms attributes.
		@type blocker_data: BlockerData
		"""
		self._cache_data["blockers"][cpv] = \
			(blocker_data.counter, tuple(str(x) for x in blocker_data.atoms))
		self._modified.add(cpv)

	def __iter__(self):
		if self._cache_data is None:
			# triggered by python-trace
			return iter([])
		return iter(self._cache_data["blockers"])

	def __delitem__(self, cpv):
		del self._cache_data["blockers"][cpv]

	def __getitem__(self, cpv):
		"""
		@rtype: BlockerData
		@returns: An object with counter and atoms attributes.
		"""
		return self.BlockerData(*self._cache_data["blockers"][cpv])

