"""Support for interfacing with the AmpliPi Multizone home audio controller."""
import logging
import operator
import re
from functools import reduce
from typing import List

import validators
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.components import media_source
from homeassistant.components.media_player import MediaPlayerDeviceClass, MediaPlayerEntity, MediaPlayerEntityFeature, MediaType
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.const import CONF_NAME, STATE_PLAYING, STATE_PAUSED, STATE_IDLE, STATE_UNKNOWN
from homeassistant.helpers.entity import DeviceInfo
from pyamplipi.amplipi import AmpliPi
from pyamplipi.models import ZoneUpdate, Source, SourceUpdate, GroupUpdate, Stream, Group, Zone, Announcement, \
    MultiZoneUpdate, PlayMedia, Status

from .const import (
    DOMAIN, AMPLIPI_OBJECT, CONF_VENDOR, CONF_VERSION, CONF_WEBAPP, )

SUPPORT_AMPLIPI_DAC = (
        MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.GROUPING
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.TURN_OFF
)

SUPPORT_AMPLIPI_ANNOUNCE = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.VOLUME_SET
)

SOURCE_SUPPORT_LOOKUP_DICT = {
    'play': MediaPlayerEntityFeature.PLAY,
    'pause': MediaPlayerEntityFeature.PAUSE,
    'stop': MediaPlayerEntityFeature.STOP,
    'next': MediaPlayerEntityFeature.NEXT_TRACK,
    'prev': MediaPlayerEntityFeature.PREVIOUS_TRACK,
    'toggle': MediaPlayerEntityFeature.TURN_OFF,
}

ZONE_SUPPORT_LOOKUP_DICT = {
    'play': MediaPlayerEntityFeature.PLAY,
    'pause': MediaPlayerEntityFeature.PAUSE,
    'stop': MediaPlayerEntityFeature.STOP,
    'next': MediaPlayerEntityFeature.NEXT_TRACK,
    'prev': MediaPlayerEntityFeature.PREVIOUS_TRACK,
    'toggle': MediaPlayerEntityFeature.TURN_OFF,
    'join': MediaPlayerEntityFeature.GROUPING,
}

STREAM_SUPPORT_LOOKUP_DICT = { #This is currently the same as the SOURCE version, I think there will still be value in having multiple versions
    'play': MediaPlayerEntityFeature.PLAY,
    'pause': MediaPlayerEntityFeature.PAUSE,
    'stop': MediaPlayerEntityFeature.STOP,
    'next': MediaPlayerEntityFeature.NEXT_TRACK,
    'prev': MediaPlayerEntityFeature.PREVIOUS_TRACK,
    'toggle': MediaPlayerEntityFeature.TURN_OFF,
}

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

def process_stream_names(streams: List[Stream]) -> List[Stream]:
    """Processes stream names to include 'AmpliPi Stream {stream.id}: {stream.name}'"""
    for stream in streams:
        # Need to check if name was already processed so you don't get stuff like:
        # "AmpliPi Stream 996: AmpliPi Stream 996: AmpliPi Stream 996: Input 1" due to back population
        if "AmpliPi Stream" not in stream.name:
            stream.name = f"AmpliPi Stream {stream.id}: {stream.name}"
    return streams

def extract_source_id_from_name(source: str):
    return int(''.join(re.findall(r'\d', source))[0]) - 1


def build_url(api_base_path, img_url):
    if img_url is None:
        return None

    # if we have a full url, go ahead and return it
    if validators.url(img_url):
        return img_url

    # otherwise it might be a relative path.
    new_url = f'{api_base_path}/{img_url}'

    if validators.url(new_url):
        return new_url

    return None

async def migrate_entities(hass, status: Status):
    """Contains every past name and ID schema to automatically migrate old schemas to the newest ones, with the intents of retaining user setups during updates"""
    # This should have minimal updates, try not to need this function
    entity_registry = await async_get_entity_registry(hass)

    sources = status.sources
    zones = status.zones
    groups = status.groups
    streams = status.streams

    for source in sources:
        # Example for how to migrate entity IDs, should we ever need to
        # Keep a list of all old schemas so updates will always be forward-compatible to the newest schema
        # old_id_schemas = [
        #     f"media_player.amplipi_source_{source.id + 1}",
        # ]
        # for schema in old_id_schemas:
        #     await entity_registry.async_update_entity(f"media_player.amplipi_source_{source.id + 1}", entity_id=f"media_player.amplipi_source_{source.id + 1}")

        await entity_registry.async_update_entity(f"media_player.amplipi_source_{source.id + 1}", name=f"AmpliPi Source: {source.name}")
    
    for zone in zones:
        await entity_registry.async_update_entity(f"media_player.amplipi_zone_{str(zone.name).replace(' ', '_').lower()}", name=f"AmpliPi Zone: {zone.name}")
    
    for group in groups:
        await entity_registry.async_update_entity(f"media_player.amplipi_group_{str(group.name).replace(' ', '_').lower()}", name=f"AmpliPi Group: {group.name}")
    
    for stream in streams:
        await entity_registry.async_update_entity(f"media_player.amplipi_stream_{stream.id}_{str(stream.name).replace(' ', '_').lower()}", name=process_stream_names([stream])[0])


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the AmpliPi MultiZone Audio Controller"""
    hass_entry = hass.data[DOMAIN][config_entry.entry_id]

    amplipi: AmpliPi = hass_entry[AMPLIPI_OBJECT]
    vendor = hass_entry[CONF_VENDOR]
    name = hass_entry[CONF_NAME]
    version = hass_entry[CONF_VERSION]
    image_base_path = f'{hass_entry[CONF_WEBAPP]}'

    status = await amplipi.get_status()

    await migrate_entities(hass, status)

    sources: list[MediaPlayerEntity] = [
        AmpliPiSource(DOMAIN, source, status.streams, vendor, version, image_base_path, amplipi)
        for source in status.sources]

    zones: list[MediaPlayerEntity] = [
        AmpliPiZone(DOMAIN, zone, None, status.streams, status.sources, vendor, version, image_base_path, amplipi)
        for zone in status.zones]

    groups: list[MediaPlayerEntity] = [
        AmpliPiZone(DOMAIN, None, group, status.streams, status.sources, vendor, version, image_base_path, amplipi)
        for group in status.groups]
    
    streams: list[MediaPlayerEntity] = [
        AmpliPiStream(DOMAIN, stream, status.sources, vendor, version, image_base_path, amplipi)
        for stream in status.streams
    ]
    
    announcer: list[MediaPlayerEntity] = [
        AmpliPiAnnouncer(DOMAIN, vendor, version, image_base_path, amplipi)
    ]

    async_add_entities(sources + zones + groups + streams + announcer)


async def async_remove_entry(hass, entry) -> None:
    pass


class AmpliPiSource(MediaPlayerEntity):
    """Representation of an AmpliPi Source Input, of which 4 are supported (Hard Coded)."""

    @property
    def should_poll(self):
        """Polling needed."""
        return True

    def __init__(self, namespace: str, source: Source, streams: List[Stream], vendor: str, version: str,
                 image_base_path: str, client: AmpliPi):
        self._streams = process_stream_names(streams)

        self._id = source.id
        self._current_stream = None
        self._image_base_path = image_base_path
        self._zones = []
        self._groups = []
        self._name = f"Source {self._id + 1}"
        self._vendor = vendor
        self._version = version
        self._source = source
        self._client = client
        self._unique_id = f"{namespace}_source_{source.id}"
        self._last_update_successful = False
        self._attr_device_class = MediaPlayerDeviceClass.RECEIVER


    async def async_turn_off(self):
        if self._source is not None:
            _LOGGER.warning(f"disconnecting stream from source {self._name}")
            await self._update_source(SourceUpdate(
                input='None'
            ))

    async def async_mute_volume(self, mute):
        if mute is None:
            return

        if self._source is not None:
            _LOGGER.warning(f"setting mute to {mute}")
            await self._update_zones(
                MultiZoneUpdate(
                    zones=[z.id for z in self._zones],
                    groups=[z.id for z in self._groups],
                    update=ZoneUpdate(
                        mute=mute,
                    )
                )
            )

    async def async_set_volume_level(self, volume):
        if volume is None:
            return
        _LOGGER.warning(f"setting volume to {volume}")
        
        group = next(filter(lambda z: z.vol_f is not None, self._groups), None)
        zone = next(filter(lambda z: z.vol_f is not None, self._zones), None)
        if group is not None:
            group.vol_f = volume
        elif zone is not None:
            zone.vol_f = volume
        
        await self._update_zones(
            MultiZoneUpdate(
                zones=[z.id for z in self._zones],
                groups=[z.id for z in self._groups],
                update=ZoneUpdate(
                    vol_f=volume
                )
            )
        )


    async def async_volume_up(self):
        if hasattr(self, "volume_up"):
            await self.hass.async_add_executor_job(self.volume_up)
            return

        if self.volume_level is not None and self.volume_level < 1:
            await self.async_set_volume_level(min(1, self.volume_level + 0.01))

    async def async_volume_down(self):
        if hasattr(self, "volume_down"):
            await self.hass.async_add_executor_job(self.volume_down)
            return

        if self.volume_level is not None and self.volume_level > 0:
            await self.async_set_volume_level(max(0, self.volume_level - 0.01))


    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_media_play(self):
        await self._client.play_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_stop(self):
        await self._client.stop_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_pause(self):
        await self._client.pause_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_previous_track(self):
        await self._client.previous_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_next_track(self):
        await self._client.next_stream(self._current_stream.id)
        await self.async_update()

    async def async_join_players(self, group_members):
        """Join `group_members` as a player group with the current player."""

    async def async_unjoin_player(self):
        """Remove this player from any group."""

    async def async_play_media(self, media_type, media_id, **kwargs):
        _LOGGER.warning(f'Play Media {media_type} {media_id} {kwargs}')

        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(self.hass, media_id)
            media_id = play_item.url
            _LOGGER.warning(f'Playing media source: {play_item} {media_id}')

        media_id = async_process_play_media_url(self.hass, media_id)
        await self._client.play_media(
            PlayMedia(
                source_id=self._source.id,
                media=media_id
            )
        )
        pass
    
    def process_stream_id(self, stream: str):
        """Pulls the stream.id out of a stream name"""
        processed = ''.join(re.findall(r'\d', stream))
        # Only return first n digits in case there's a stream name with numbers in it
        if int(processed[0]) == 1:
            return processed[:4]
        elif int(processed[0]) == 9:
            return processed[:3]
        else:
            _LOGGER.error("AmpliPiSource.process_stream_id() could not determine stream ID")

    async def async_select_source(self, source):
        if self._source is not None and self._source.name == source:
            await self._update_source(SourceUpdate(
                input='local'
            ))
        elif source == 'None':
            await self._update_source(SourceUpdate(
                input='None'
            ))
        else:
            # Process both the input and the known name in case the entity_id is sent back for processing
            stream = next(filter(lambda z: self.process_stream_id(z.name) == self.process_stream_id(source), self._streams), None)
            if stream is None:
                _LOGGER.warning(f'Select Source {source} called but a match could not be found in the stream cache, '
                                f'{self._streams}')
                pass
            else:
                await self._update_source(SourceUpdate(
                    input=f'stream={stream.id}'
                ))

    def clear_playlist(self):
        pass

    def set_shuffle(self, shuffle):
        pass

    def set_repeat(self, repeat):
        pass

    def build_url(self, img_url):
        if img_url is None:
            return None

        # if we have a full url, go ahead and return it
        if validators.url(img_url):
            return img_url

        # otherwise it might be a relative path.
        new_url = f'{self._image_base_path}/{img_url}'

        if validators.url(new_url):
            return new_url

        return None

    @property
    def supported_features(self):
        """Return flag of media commands that are supported."""

        supported_features = SUPPORT_AMPLIPI_DAC
        if self._source is not None and self._source.info is not None and len(self._source.info.supported_cmds) > 0:
            supported_features = supported_features | reduce(
                operator.or_,
                [
                    SOURCE_SUPPORT_LOOKUP_DICT.get(key) for key
                    in (SOURCE_SUPPORT_LOOKUP_DICT.keys() & self._source.info.supported_cmds)
                ]
            )

        return supported_features

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return MediaType.MUSIC

    @property
    def entity_registry_enabled_default(self):
        """Return if the entity should be enabled when first added to the entity registry."""
        return True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            model="AmpliPi MultiZone Source",
            name=self._name,
            manufacturer=self._vendor,
            sw_version=self._version,
            configuration_url=self._image_base_path,
        )

    # name: str | None
    # connections: set[tuple[str, str]]
    # identifiers: set[tuple[str, str]]
    # manufacturer: str | None
    # model: str | None
    # suggested_area: str | None
    # sw_version: str | None
    # via_device: tuple[str, str]
    # entry_type: str | None
    # default_name: str
    # default_manufacturer: str
    # default_model: str

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the source"""
        return "AmpliPi Source: " + self._name

    async def async_update(self):
        """Retrieve latest state."""
        _LOGGER.info(f'Retrieving state for source {self._source.id}')

        try:
            state = await self._client.get_status()
            source = next(filter(lambda z: z.id == self._source.id, state.sources), None)
            streams = state.streams
        except Exception:
            self._last_update_successful = False
            _LOGGER.error(f'Could not update source {self._source.id}')
            return

        if not source:
            self._last_update_successful = False
            return

        groups = list(filter(lambda z: z.source_id == self._source.id, state.groups))
        zones = list(filter(lambda z: z.source_id == self._source.id, state.zones))

        self.sync_state(source, streams, zones, groups)

    def sync_state(self, state: Source, streams: List[Stream], zones: List[Zone], groups: List[Group]):
        self._source = state

        self._streams = process_stream_names(streams)

        self._current_stream = None

        if 'stream=' in state.input and 'stream=local' not in state.input:
            stream_id = int(self._source.input.split('=')[1])
            self._current_stream = next(filter(lambda z: z.id == stream_id, self._streams), None)

        self._zones = zones
        self._groups = groups
        self._last_update_successful = True
        self._name = state.name

        info = self._source.info

        if info is not None:
            track_name = info.track
            if track_name is None:
                track_name = info.name

            self._attr_media_album_artist = info.artist
            self._attr_media_album_name = info.album
            self._attr_media_title = track_name
            self._attr_media_track = info.track
            if self._current_stream is not None:
                self._attr_app_name = self._current_stream.type
            else:
                self._attr_app_name = None
            self._attr_media_image_url = build_url(self._image_base_path, info.img_url)
            self._attr_media_channel = info.station
        else:
            self._attr_media_album_artist = None
            self._attr_media_album_name = None
            self._attr_media_title = None
            self._attr_media_track = None
            self._attr_app_name = None
            self._attr_media_image_url = None
            self._attr_media_channel = None

    @property
    def state(self):
        """Return the state of the zone."""
        if self._last_update_successful is False:
            return STATE_UNKNOWN
        elif self._source is None or self._source.info is None or self._source.info.state is None:
            return STATE_IDLE
        elif self._source.info.state in (
                'paused'
        ):
            return STATE_PAUSED
        elif self._source.info.state in (
                'playing'
        ):
            return STATE_PLAYING
        elif self._source.info.state in (
                'stopped'
        ):
            return STATE_IDLE
        elif self._source.info.state in (
                'stopped'
        ):
            return STATE_IDLE

        return STATE_IDLE

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        # if self._source.vol_delta is None:
        group = next(filter(lambda z: z.vol_f is not None, self._groups), None)
        zone = next(filter(lambda z: z.vol_f is not None, self._zones), None)
        if group is not None:
            return group.vol_f
        elif zone is not None:
            return zone.vol_f
        return STATE_UNKNOWN

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        group = next(filter(lambda z: z.mute is not None, self._groups), None)
        zone = next(filter(lambda z: z.mute is not None, self._zones), None)
        if group is not None:
            return group.mute
        elif zone is not None:
            return zone.mute
        return STATE_UNKNOWN

    @property
    def source(self):
        if self._source is not None:
            if self._source.input == 'local':
                return self._source.name
            elif self._current_stream is not None:
                return self._current_stream.name
        return 'None'

    @property
    def source_list(self):
        """List of available input sources."""
        streams = ['None']
        streams += [stream.name for stream in self._streams if stream.id >= 1000 or stream.id - 996 == self._id]
        return streams

    async def _update_source(self, update: SourceUpdate):
        await self._client.set_source(self._source.id, update)
        await self.async_update()

    async def _update_zones(self, update: MultiZoneUpdate):
        # zones = await self._client.get_zones()
        # associated_zones = filter(lambda z: z.source_id == self._source.id, zones)
        await self._client.set_zones(update)
        await self.async_update()

    async def _update_groups(self, update: GroupUpdate):
        groups = await self._client.get_groups()
        associated_groups = filter(lambda g: g.source_id == self._source.id, groups)
        for group in associated_groups:
            await self._client.set_group(group.id, update)
        await self.async_update()

    @property
    def extra_state_attributes(self):
        zone_list = []
        for zone in self._zones:
            zone_list.append(zone.id)
        return {"amplipi_source_id" : self._id,
                "amplipi_source_zones" : zone_list}

class AmpliPiZone(MediaPlayerEntity):
    """Representation of an AmpliPi Zone and/or Group. Supports Audio volume
        and mute controls and the ability to change the current 'source' a
        zone is tied to"""

    @property
    def should_poll(self):
        """Polling needed."""
        return True

    async def async_turn_on(self):
        if self._is_group:
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=ZoneUpdate(
                        disabled=False,
                    )
                )
            )
        else:
            await self._update_zone(
                ZoneUpdate(
                    disabled=False,
                )
            )
        #self.is_on = True

    def __init__(self, namespace: str, zone, group,
                 streams: List[Stream], sources: List[Source],
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPi):
        self._current_source = None
        self._sources = sources
        self._is_group = group is not None

        if self._is_group:
            self._id = group.id
            self._name = group.name
            self._unique_id = f"{namespace}_group_{self._id}"
        else:
            self._id = zone.id
            self._name = zone.name
            self._unique_id = f"{namespace}_zone_{self._id}"

        self._streams = process_stream_names(streams)
        self._image_base_path = image_base_path
        self._vendor = vendor
        self._version = version
        self._zone = zone
        self._group = group
        self._enabled = False
        self._client = client
        self._last_update_successful = False
        self._attr_source_list = [
            'Source 1',
            'Source 2',
            'Source 3',
            'Source 4',
        ]
        self._available = False
        self._extra_attributes = []
        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER

    async def async_turn_off(self):
        if self._current_source is not None:
            if self._is_group:
                _LOGGER.info(f"Disconnecting zones from source {self._current_source}")
                await self._update_group(
                    MultiZoneUpdate(
                        groups=[self._group.id],
                        update=ZoneUpdate(
                            source_id=-1,
                        )
                    )
                )
            else:
                _LOGGER.info(f"Disconnecting zone from source {self._current_source}")
                await self._update_zone(ZoneUpdate(
                    source_id=-1,
                ))

    async def async_mute_volume(self, mute):
        if mute is None:
            return
        _LOGGER.info(f"setting mute to {mute}")
        if self._is_group:
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=ZoneUpdate(
                        mute=mute,
                    )
                )
            )
        else:
            await self._update_zone(ZoneUpdate(
                mute=mute
            ))

    async def async_set_volume_level(self, volume):
        if volume is None:
            return
        
        if self._is_group and self._group is not None:
            self._group.vol_f = volume
        elif self._zone is not None:
            self._zone.vol_f = volume
    
        _LOGGER.info(f"setting volume to {volume}")
        if self._is_group:
            await self._update_group(
                MultiZoneUpdate(
                    groups=[self._group.id],
                    update=ZoneUpdate(
                        vol_f=volume
                    )
                )
            )
        else:
            await self._update_zone(ZoneUpdate(
                vol_f=volume
            ))


    async def async_volume_up(self):
        if hasattr(self, "volume_up"):
            await self.hass.async_add_executor_job(self.volume_up)
            return

        if self.volume_level is not None and self.volume_level < 1:
            await self.async_set_volume_level(min(1, self.volume_level + 0.01))

    async def async_volume_down(self):
        if hasattr(self, "volume_down"):
            await self.hass.async_add_executor_job(self.volume_down)
            return

        if self.volume_level is not None and self.volume_level > 0:
            await self.async_set_volume_level(max(0, self.volume_level - 0.01))

    @property
    def supported_features(self):
        """Return flag of media commands that are supported."""

        supported_features = SUPPORT_AMPLIPI_DAC
        if self._current_source is not None and self._current_source.info is not None and len(self._current_source.info.supported_cmds) > 0:
            supported_features = supported_features | reduce(
                operator.or_,
                [
                    ZONE_SUPPORT_LOOKUP_DICT.get(key) for key
                    in (ZONE_SUPPORT_LOOKUP_DICT.keys() & self._current_source.info.supported_cmds)
                ]
            )
        return supported_features

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return "speaker"

    @property
    def entity_registry_enabled_default(self):
        """Return if the entity should be enabled when first added to the entity registry."""
        return True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        if self._is_group:
            model = "AmpliPi Group"
        else:
            model = "AmpliPi Zone"

        via_device = None

        if self._current_source is not None:
            via_device = (DOMAIN, f"{DOMAIN}_source_{self._current_source.id}")

        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            model=model,
            name=self._name,
            manufacturer=self._vendor,
            sw_version=self._version,
            configuration_url=self._image_base_path,
            via_device=via_device,
        )

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the zone."""
        if self._is_group:
            return "AmpliPi Group: " + self._name
        else:
            return "AmpliPi Zone: " + self._name

    async def async_update(self):
        """Retrieve latest state."""
        _LOGGER.info(f'Retrieving state for source {self._id}')

        zone = None
        group = None
        enabled = False

        try:
            state = await self._client.get_status()
            if self._is_group:
                group = next(filter(lambda z: z.id == self._id, state.groups), None)
                if not group:
                    self._last_update_successful = False
                    return
                any_enabled_zone = next(filter(lambda z: z.id in group.zones, state.zones), None)

                if any_enabled_zone is not None:
                    enabled = True
            else:
                zone = next(filter(lambda z: z.id == self._id, state.zones), None)
                if not zone:
                    self._last_update_successful = False
                    return
                enabled = not zone.disabled
            streams = state.streams
        except Exception:
            self._last_update_successful = False
            _LOGGER.error(f'Could not update {"group" if self._is_group else "zone"} {self._id}')
            return

        await self._get_extra_attributes()
        self._available = await self._update_available()
        self.sync_state(zone, group, streams, state.sources, enabled)

    def sync_state(self, zone: Zone, group: Group, streams: List[Stream],
                   sources: List[Source], enabled: bool):
        self._zone = zone
        self._group = group
        self._streams = process_stream_names(streams)
        self._sources = sources
        self._last_update_successful = True
        self._enabled = enabled

        info = None
        self._current_source = None

        if self._is_group:
            self._current_source = next(filter(lambda s: self._group.source_id == s.id, sources), None)
        elif self._zone.source_id is not None:
            self._current_source = next(filter(lambda s: self._zone.source_id == s.id, sources), None)

        if self._current_source is not None:
            info = self._current_source.info

        if self._current_source is not None and 'stream=' in self._current_source.input and 'stream=local' not in self._current_source.input:
            stream_id = int(self._current_source.input.split('=')[1])
            self._current_stream = next(filter(lambda z: z.id == stream_id, self._streams), None)

        if info is not None:
            self._attr_media_album_artist = info.artist
            self._attr_media_album_name = info.album
            self._attr_media_title = info.name
            self._attr_media_track = info.track
            self._attr_media_image_url = build_url(self._image_base_path, info.img_url)
            self._attr_media_channel = info.station
        else:
            self._attr_media_album_artist = None
            self._attr_media_album_name = None
            self._attr_media_title = None
            self._attr_media_track = None
            self._attr_media_image_url = None
            self._attr_media_channel = None

    @property
    def state(self):
        """Return the state of the zone."""
        if self._last_update_successful is False:
            return STATE_UNKNOWN
        elif self._current_source is None or self._current_source == -1 or self._current_source.info is None or self._current_source.info.state is None:
            return STATE_IDLE
        elif self._current_source.info.state in (
                'paused'
        ):
            return STATE_PAUSED
        elif self._current_source.info.state in (
                'playing'
        ):
            return STATE_PLAYING
        elif self._current_source.info.state in (
                'stopped'
        ):
            return STATE_IDLE
        elif self._current_source.info.state in (
                'stopped'
        ):
            return STATE_IDLE

        return STATE_IDLE


    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        if self._is_group and self._group is not None:
            return self._group.vol_f
        elif self._zone is not None:
            return self._zone.vol_f
        return None

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        if self._is_group:
            return self._group.mute
        else:
            return self._zone.mute

    async def async_select_source(self, source):
        # source can either be the name of a source or the source's entity ID depending on if this function was called from an automation or directly from the dropdown
        # Either way, it will contain the ID number in it, use a regex to only return digits and then only use the first one in case we ever allow users to name their sources
        source_id = extract_source_id_from_name(source)
        self._current_source = source
        if source_id is not None:
            if self._is_group:
                await self._update_group(
                    MultiZoneUpdate(
                        groups=[self._group.id],
                        update=ZoneUpdate(
                            source_id=source_id
                        )
                    )
                )
            else:
                await self._update_zone(
                    ZoneUpdate(
                        source_id=source_id
                    )
                )
            await self.async_update()

    async def _update_zone(self, update: ZoneUpdate):
        await self._client.set_zone(self._id, update)
        await self.async_update()

    async def _update_group(self, update: MultiZoneUpdate):
        await self._client.set_zones(update)
        await self.async_update()

    @property
    def source_list(self):
        """List of available input sources."""
        source_list = []
        source_num = 1
        if self._sources is not None:
            for _ in self._sources:
                source_list.append("Source " + str(source_num))
                source_num += 1
        return source_list

    @property
    def source(self):
        """Returns the current source playing, if this is wrong it won't show up as the selected source on HomeAssistant"""
        if self._current_source is not None:
            if self._current_source == "None":
                return "None"
            return f'Source {self._current_source.id + 1}'
        return None

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_play_media(self, media_type, media_id, **kwargs):
        _LOGGER.warning(f'Play Media {media_type} {media_id} {kwargs}')

        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(self.hass, media_id)
            media_id = play_item.url
            _LOGGER.warning(f'Playing media source: {play_item} {media_id}')

        #No source, see if we can find an empty one
        if self._current_source is None:
            sources = await self._client.get_sources()
            for source in sources:
                if source is not None and (source.input == '' or source.input == 'None' or source.input is None):
                    self._current_source = source
            
            if self._current_source is None:
                raise Exception("Not attached to a source and all sources are in use. Clear out a source or select an already existing one and try again.")
                

        media_id = async_process_play_media_url(self.hass, media_id)
        await self._client.play_media(
            PlayMedia(
                source_id=self._current_source.id,
                media=media_id,
            )
        )
        pass

    @property
    def available(self):
        return self._available

    @property
    def extra_state_attributes(self):
        return self._extra_attributes

    async def _get_extra_attributes(self):
        if self._is_group:
            state = await self._client.get_status()
            zone_ids = []

            for zone_id in self._group.zones:
                for state_zone in state.zones:
                    if state_zone.id == zone_id and not state_zone.disabled:
                        zone_ids.append(zone_id)
            self._extra_attributes = {"amplipi_zones" : zone_ids}

            #if self._zone_num_cache != len(zone_ids):
                #self.hass.bus.fire("group_change_event", {"group_change": True})
        else:
            self._extra_attributes = {"amplipi_zone_id" : self._zone.id}

    async def _update_available(self):
        state = await self._client.get_status()
        if self._is_group:
            for zone_id in self._group.zones:
                for state_zone in state.zones:
                    if state_zone.id == zone_id and not state_zone.disabled:
                        return True
            return False
        elif self._zone is None or self._zone.disabled:
            return False
        return True

    async def async_media_play(self):
        await self._client.play_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_stop(self):
        await self._client.stop_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_pause(self):
        await self._client.pause_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_previous_track(self):
        await self._client.previous_stream(self._current_stream.id)
        await self.async_update()

    async def async_media_next_track(self):
        await self._client.next_stream(self._current_stream.id)
        await self.async_update()

class AmpliPiAnnouncer(MediaPlayerEntity):
    
    @property
    def should_poll(self):
        """Polling needed."""
        return True

    def __init__(self, namespace: str,
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPi):
        self._current_source = None

        self._unique_id = f"{namespace}_announcement"
        self._vendor = vendor
        self._version = version
        self._enabled = True
        self._client = client
        self._last_update_successful = True
        self._available = True
        self._extra_attributes = []
        self._image_base_path = image_base_path
        self._name = "AmpliPi Announcement"
        self._volume = 0.5
        self._attr_device_class = MediaPlayerDeviceClass.SPEAKER

    @property
    def available(self):
        return self._available
    
    @property
    def supported_features(self):
        self._attr_app_name = "AmpliPi Announcement Channel"
        return SUPPORT_AMPLIPI_ANNOUNCE
    
    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return MediaType.TRACK
    
    @property
    def entity_registry_enabled_default(self):
        """Return if the entity should be enabled when first added to the entity registry."""
        return True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        model = "AmpliPi Announcement Channel"

        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            model=model,
            name=self._name,
            manufacturer=self._vendor,
            sw_version=self._version,
            configuration_url=self._image_base_path
        )

    @property
    def volume_level(self):
        return self._volume

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the zone."""
        return "AmpliPi: " + self._name

    @property
    def state(self):
        return STATE_IDLE

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_play_media(self, media_type, media_id, **kwargs):
        _LOGGER.warning(f'Play Media {media_type} {media_id} {kwargs}')
        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(self.hass, media_id)
            media_id = play_item.url
            _LOGGER.warning(f'Playing media source: {play_item} {media_id}')

        media_id = async_process_play_media_url(self.hass, media_id)
        await self._client.announce(
            Announcement(
                media=media_id,
                vol_f=self._volume
            )
        )
        pass


    async def async_set_volume_level(self, volume):
        if volume is None:
            return
        self._volume = volume

class AmpliPiStream(MediaPlayerEntity):
    """Representation of an AmpliPi Stream. Supports Audio volume
        and mute controls and the ability to change the current 'source' a
        stream is tied to"""

    @property
    def should_poll(self):
        """Polling needed."""
        return True

    async def async_turn_on(self): # I would like for this to be supported, but I cannot figure out how yet
        await self.find_source()

    def __init__(self, namespace: str, stream: Stream,
                 sources: List[Source],
                 vendor: str, version: str, image_base_path: str,
                 client: AmpliPi):
        self._stream = process_stream_names([stream])[0]
        self._current_source = None
        self._current_zones = []
        self._current_groups = []
        self._sources = sources

        self._id = stream.id
        self._name = stream.name
        self._unique_id = stream.name
        
        self._image_base_path = image_base_path
        self._vendor = vendor
        self._version = version
        self._client = client
        self._last_update_successful = False
        # not a real device class, but allows us to match streams and only streams with the start_streaming blueprint's streams dropdown
        self._attr_device_class = "stream"
        self._attr_source_list = [
            'Source 1',
            'Source 2',
            'Source 3',
            'Source 4',
        ]
        self._available = False
        self._extra_attributes = []

    async def _update_source(self, source_id, update: SourceUpdate):
        await self._client.set_source(source_id, update)
        await self.async_update()

    async def _update_zones(self, update: MultiZoneUpdate):
        if self._current_source is not None:
            zones = await self._client.get_zones()
            update.zones = filter(lambda z: z.source_id == self._current_source.id, zones)
            await self._client.set_zones(update)
            await self.async_update()

    async def async_turn_off(self):
        if self._current_source is not None:
            _LOGGER.info(f"Disconnecting stream from source {self._current_source}")
            await self._update_source(
                self._current_source.id,
                SourceUpdate(
                    input='None'
                )
            )

    async def async_mute_volume(self, mute):
        if mute is None:
            return

        if self._current_source is not None:
            _LOGGER.warning(f"setting mute to {mute}")
            await self._update_zones(
                MultiZoneUpdate(
                    update=ZoneUpdate(
                        mute=mute,
                    )
                )
            )

    async def async_set_volume_level(self, volume):
        if volume is None:
            return
        await self._update_zones(
            MultiZoneUpdate(
                groups=self._current_groups,
                zones=self._current_zones,
                update=ZoneUpdate(
                    vol_f=volume
                )
            )
        )

    async def async_volume_up(self):
        if hasattr(self, "volume_up"):
            await self.hass.async_add_executor_job(self.volume_up)
            return

        if self.volume_level is not None and self.volume_level < 1:
            await self.async_set_volume_level(min(1, self.volume_level + 0.01))

    async def async_volume_down(self):
        if hasattr(self, "volume_down"):
            await self.hass.async_add_executor_job(self.volume_down)
            return

        if self.volume_level is not None and self.volume_level > 0:
            await self.async_set_volume_level(max(0, self.volume_level - 0.01))

    @property
    def supported_features(self):
        """Return flag of media commands that are supported."""

        supported_features = SUPPORT_AMPLIPI_DAC
        if self._current_source is not None and self._current_source.info is not None and len(self._current_source.info.supported_cmds) > 0:
            supported_features = supported_features | reduce(
                operator.or_,
                [
                    STREAM_SUPPORT_LOOKUP_DICT.get(key) for key
                    in (STREAM_SUPPORT_LOOKUP_DICT.keys() & self._current_source.info.supported_cmds)
                ]
            )
        return supported_features

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return "speaker"

    @property
    def entity_registry_enabled_default(self):
        """Return if the entity should be enabled when first added to the entity registry."""
        return True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        via_device = None
        if self._current_source is not None:
            via_device = (DOMAIN, f"{DOMAIN}_source_{self._current_source.id}")

        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            model="AmpliPi Stream",
            name=self._name,
            manufacturer=self._vendor,
            sw_version=self._version,
            configuration_url=self._image_base_path,
            via_device=via_device,
        )

    @property
    def unique_id(self):
        """Return unique ID for this device."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the stream."""
        return self._name

    async def async_update(self):
        """Retrieve latest state."""
        _LOGGER.info(f'Retrieving state for stream {self._id}')
        stream = None
        groups = []
        zones = []

        try:
            state = await self._client.get_status()
            stream = next(filter(lambda s: s.id == self._id, state.streams), None)
            if stream is not None:
                current_source = next((s for s in state.sources if s.input == f"stream={stream.id}"), None)
                if current_source is not None:
                    for group in state.groups:
                        if group.source_id == current_source.id:
                            groups.append(group)

                    for zone in state.zones:
                        if zone.source_id == current_source.id:
                            zones.append(zone)
        except Exception as e:
            self._last_update_successful = False
            _LOGGER.error(f'Could not update stream {self._id} due to error:')
            _LOGGER.error(e)
            return

        await self._get_extra_attributes()
        self._available = await self._update_available()
        self.sync_state(stream, state.sources, current_source, zones, groups)


    def sync_state(self, stream: Stream, sources: List[Source], current_source, zones, groups):
        self._stream = process_stream_names([stream])[0]
        self._sources = sources
        self._current_source = current_source
        self._last_update_successful = True
        self._current_zones = zones
        self._current_groups = groups

        info = None

        if self._current_source is not None:
            info = self._current_source.info

        if info is not None:
            self._attr_media_album_artist = info.artist
            self._attr_media_album_name = info.album
            self._attr_media_title = info.name
            self._attr_media_track = info.track
            self._attr_media_image_url = build_url(self._image_base_path, info.img_url)
            self._attr_media_channel = info.station
        else:
            self._attr_media_album_artist = None
            self._attr_media_album_name = None
            self._attr_media_title = None
            self._attr_media_track = None
            self._attr_media_image_url = None
            self._attr_media_channel = None

    @property
    def state(self):
        """Return the state of the stream."""
        if self._last_update_successful is False:
            return STATE_UNKNOWN
        elif self._current_source is None or self._current_source.id == -1 or self._current_source.info is None or self._current_source.info.state is None:
            return STATE_IDLE
        elif self._current_source.info.state in (
                'paused'
        ):
            return STATE_PAUSED
        elif self._current_source.info.state in (
                'playing'
        ):
            return STATE_PLAYING
        elif self._current_source.info.state in (
                'stopped'
        ):
            return STATE_IDLE
        return STATE_IDLE


    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        if self._current_source is not None:

            group = next(filter(lambda g: g.vol_f is not None, self._current_groups), None)
            if group is not None:
                return group.vol_f

            zone = next(filter(lambda z: z.vol_f is not None, self._current_zones), None)
            if zone is not None:
                return zone.vol_f
            
        return None

    @property
    def is_volume_muted(self) -> bool:
        """Boolean if volume is currently muted."""
        if self._current_source is not None:

            group = next(filter(lambda z: z.mute is not None, self._current_groups), None)
            if group is not None:
                return group.mute
            
            zone = next(filter(lambda z: z.mute is not None, self._current_zones), None)
            if zone is not None:
                return zone.mute
        return True

    async def async_select_source(self, source):
        source_id = extract_source_id_from_name(source)
        self._current_source = next((s for s in self._sources if s.id == source_id), self._current_source)
        await self._update_source(
            source_id,
            SourceUpdate(
                input=f'stream={self._id}'
            )
        )
        await self.async_update()

    @property
    def source_list(self):
        """List of available input sources."""
        source_list = []
        source_num = 1
        if self._sources is not None:
            for _ in self._sources:
                source_list.append("Source " + str(source_num))
                source_num += 1
        return source_list

    @property
    def source(self):
        """Returns the current source playing, if this is wrong it won't show up as the selected source on HomeAssistant"""
        if self._current_source is not None:
            if self._current_source == "None":
                return "None"
            return f'Source {self._current_source.id + 1}'
        return None

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_play_media(self, media_type, media_id, **kwargs):
        _LOGGER.warning(f'Play Media {media_type} {media_id} {kwargs}')

        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(self.hass, media_id)
            media_id = play_item.url
            _LOGGER.warning(f'Playing media source: {play_item} {media_id}')

        await self.find_source()

        media_id = async_process_play_media_url(self.hass, media_id)
        await self._client.play_media(
            PlayMedia(
                source_id=self._current_source.id,
                media=media_id,
            )
        )
        pass

    async def find_source(self):
        """Connects stream to first available source"""
        # No source, see if we can find an empty one
        if self._current_source is None:
            sources = await self._client.get_sources()
            for source in sources:
                if source is not None and (source.input == '' or source.input == 'None' or source.input is None):
                    self._current_source = source
            
            if self._current_source is None:
                raise Exception("Not attached to a source and all sources are in use. Clear out a source or select an already existing one and try again.")

    @property
    def available(self):
        return self._available

    @property
    def extra_state_attributes(self):
        return self._extra_attributes

    async def _get_extra_attributes(self):
        if self._current_source is not None:
            self._extra_attributes = {"amplipi_source_id" : self._current_source.id }
        else:
            self._extra_attributes = {"amplipi_source_id" : None }

    async def _update_available(self):
        if self._stream is None:
            return False
        return True

    async def async_media_play(self):
        await self._client.play_stream(self._stream.id)
        await self.async_update()

    async def async_media_stop(self):
        await self._client.stop_stream(self._stream.id)
        await self.async_update()

    async def async_media_pause(self):
        await self._client.pause_stream(self._stream.id)
        await self.async_update()

    async def async_media_previous_track(self):
        await self._client.previous_stream(self._stream.id)
        await self.async_update()

    async def async_media_next_track(self):
        await self._client.next_stream(self._stream.id)
        await self.async_update()
