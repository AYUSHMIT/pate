{-# LANGUAGE FlexibleContexts #-}
{-# LANGUAGE GADTs #-}
{-# LANGUAGE RankNTypes #-}
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE LambdaCase #-}
module Pate.Discovery.ParsedFunctions (
    ParsedFunctionMap
  , newParsedFunctionMap
  , parsedFunctionContaining
  , parsedBlocksContaining
  , parsedBlockEntry
  , isFunctionStart
  , findFunctionByName
  , resolveFunctionEntry
  , setAbstractState
  , getInitAbsDomain
  , ParsedBlocks(..)
  ) where


import           Control.Lens ( (^.), (&), (.~) )
import qualified Data.Foldable as F
import qualified Data.IORef as IORef
import qualified Data.Map as Map
import           Data.Maybe ( mapMaybe, isJust )
import qualified Data.Parameterized.Classes as PC
import qualified Data.Parameterized.Map as MapF
import           Data.Parameterized.Some ( Some(..), viewSome )
import qualified Data.Set as Set
import qualified Prettyprinter as PP
import qualified Prettyprinter.Render.Text as PPT
import qualified System.Directory as SD
import           System.FilePath ( (</>), (<.>) )
import qualified System.IO as IO

import qualified Data.Macaw.AbsDomain.AbsState as DMAA
import qualified Data.Macaw.AbsDomain.JumpBounds as DMAJ
import qualified Data.Macaw.Architecture.Info as MAI
import qualified Data.Macaw.CFG as MM
import qualified Data.Macaw.Discovery as MD
import qualified Data.Macaw.Discovery.State as MD
import qualified Data.Macaw.Discovery.ParsedContents as DMDP
import qualified What4.Interface as W4

import qualified Pate.Address as PA
import qualified Pate.Binary as PBi
import qualified Pate.Block as PB
import qualified Pate.Config as PC
import qualified Pate.Memory as PM
import qualified Pate.Register.Traversal as PRt

data ParsedBlocks arch = forall ids. ParsedBlocks [MD.ParsedBlock arch ids]

data ParsedFunctionState arch bin =
  ParsedFunctionState { parsedFunctionCache :: Map.Map (PB.FunctionEntry arch bin) (Some (MD.DiscoveryFunInfo arch))
                      , discoveryState :: MD.DiscoveryState arch
                      , functionAbstractStates :: Map.Map (MM.ArchSegmentOff arch) (DMAA.AbsBlockState (MM.ArchReg arch))
                      }

-- | A (lazily constructed) map of function entry points to the blocks for that function
--
-- Think of this as a cache of discovered functions, with some of the extra
-- macaw metadata (e.g., the enclosing DiscoveryFunInfo) discarded
data ParsedFunctionMap arch bin =
  ParsedFunctionMap { parsedStateRef :: IORef.IORef (ParsedFunctionState arch bin)
                    -- ^ State that is updated on-demand
                    , persistenceDir :: Maybe FilePath
                    -- ^ The directory to save discovered CFGs to, if present
                    , patchData :: PC.PatchData
                    -- ^ Extra hints provided from the user to guide code discovery
                    --
                    -- The information we use out of this is a list of functions
                    -- that the analysis should ignore. See Note [Ignored
                    -- Functions] for details.
                    , pfmInitState :: MD.DiscoveryState arch
                    -- ^ initial discovery state
                    }

-- | Allocate a new empty 'ParsedFunctionMap'
newParsedFunctionMap
  :: MM.Memory (MM.ArchAddrWidth arch)
  -- ^ The binary memory
  -> MD.AddrSymMap (MM.ArchAddrWidth arch)
  -- ^ Symbols assigned to addresses by metadata
  -> MAI.ArchitectureInfo arch
  -- ^ Architecture-specific code discovery information and configuration
  -> Maybe FilePath
  -- ^ The path to save discovered CFGs to, if present
  -> PC.PatchData
  -- ^ User-provided guidance about the patch structure and verification strategy
  -> IO (ParsedFunctionMap arch bin)
newParsedFunctionMap mem syms archInfo mCFGDir pd = do
  ref <- IORef.newIORef s0
  return ParsedFunctionMap { parsedStateRef = ref
                           , persistenceDir = mCFGDir
                           , patchData = pd
                           , pfmInitState = ds0
                           }
  where
    ds0 = MD.emptyDiscoveryState mem syms archInfo
    s0 = ParsedFunctionState { parsedFunctionCache = mempty
                             , functionAbstractStates = mempty
                             , discoveryState = ds0
                             }

funInfoToFunEntry ::
  PBi.WhichBinaryRepr bin ->
  MD.DiscoveryFunInfo arch ids ->
  PB.FunctionEntry arch bin
funInfoToFunEntry binRepr dfi =
  PB.FunctionEntry
  { PB.functionSegAddr = MD.discoveredFunAddr dfi
  , PB.functionSymbol  = MD.discoveredFunSymbol dfi
  , PB.functionBinRepr = binRepr
  }

buildParsedBlocks :: MD.DiscoveryFunInfo arch ids -> ParsedBlocks arch
buildParsedBlocks dfi = ParsedBlocks (Map.elems (dfi ^. MD.parsedBlocks))

-- | Write the macaw CFG for the given discovered function to disk if we are
-- given a directory to store it in.
--
-- Note that this directory is the *base* directory; we use the type repr for
-- the binary (original vs patched) to ensure that we don't overwrite results
-- from different binaries on disk.
saveCFG
  :: (MM.ArchConstraints arch)
  => Maybe FilePath
  -> PBi.WhichBinaryRepr bin
  -> MD.DiscoveryFunInfo arch ids
  -> IO ()
saveCFG mCFGDir repr dfi =
  F.forM_ mCFGDir $ \cfgDir -> do
    let baseDir = cfgDir </> show repr
    SD.createDirectoryIfMissing True baseDir
    let fname = baseDir </> show (MD.discoveredFunAddr dfi) <.> "cfg"
    IO.withFile fname IO.WriteMode $ \hdl -> do
      PPT.hPutDoc hdl (PP.pretty dfi)

segoffAddrsFor
  :: (MM.MemWidth w)
  => MM.Memory w
  -> PC.Address
  -> Maybe (MM.MemSegmentOff w)
segoffAddrsFor mem (PC.Address w) = do
  PM.resolveAbsoluteAddress mem (fromIntegral w)

-- | An empty macaw function that has no effect on its arguments or memory
--
-- This is meant to be used as a stand-in for any function that the user has
-- specified should be ignored.
emptyFunction
  :: (MM.RegisterInfo (MM.ArchReg arch))
  => MM.MemSegmentOff (MM.ArchAddrWidth arch)
  -- ^ The address of the function entry point
  -> MD.DiscoveryFunInfo arch x
emptyFunction faddr =
  MD.DiscoveryFunInfo { MD.discoveredFunReason = MD.UserRequest
                      , MD.discoveredFunAddr = faddr
                      , MD.discoveredFunSymbol = Nothing
                      , MD._parsedBlocks = Map.singleton faddr emptyBlock
                      , MD.discoveredClassifyFailureResolutions = []
                      }
  where
    retArgs = MM.mkRegState MM.Initial
    emptyBlock = DMDP.ParsedBlock { DMDP.pblockAddr = faddr
                                  , DMDP.blockSize = 4
                                  , DMDP.pblockStmts = []
                                  , DMDP.pblockTermStmt = DMDP.ParsedReturn retArgs
                                  , DMDP.blockReason = DMDP.FunctionEntryPoint
                                  , DMDP.blockAbstractState = DMAA.fnStartAbsBlockState faddr MapF.empty []
                                  , DMDP.pblockPrecond = Left "No block preconditions because this is a synthetic block"
                                  , DMDP.blockJumpBounds = DMAJ.functionStartBounds
                                  }

getInitAbsDomain ::
  ParsedFunctionMap arch bin ->
  PB.FunctionEntry arch bin ->
  DMAA.AbsBlockState (MM.ArchReg arch)
getInitAbsDomain (ParsedFunctionMap _ _ _ initSt) fnEntry =
  let
    faddr = PB.functionSegAddr fnEntry
    archInfo = MD.archInfo initSt
  in MAI.mkInitialAbsState archInfo (MD.memory initSt) faddr


eqAbsStates ::
  MM.RegisterInfo (MM.ArchReg arch) =>
  DMAA.AbsBlockState (MM.ArchReg arch) ->
  DMAA.AbsBlockState (MM.ArchReg arch) ->
  Bool
eqAbsStates st1 st2 = case (DMAA.joinAbsBlockState st1 st2, DMAA.joinAbsBlockState st2 st1) of
  (Nothing, Nothing) -> True
  _ -> False

-- | Modify the discovery state to inject additional abstract state information
--   when analyzing the given function
-- TODO: should setting the abstract state of an already explored function
-- be considered an error? Right now we just delete the corresponding entry in the cache
setAbstractState ::
  MM.ArchConstraints arch =>
  MM.RegisterInfo (MM.ArchReg arch) =>
  PB.FunctionEntry arch bin ->
  DMAA.AbsBlockState (MM.ArchReg arch) ->
  ParsedFunctionMap arch bin ->
  IO ()
setAbstractState fnEntry absSt0 pfm@(ParsedFunctionMap pfmRef _ _ initSt) = do
  let faddr = PB.functionSegAddr fnEntry
  let archInfo = MD.archInfo initSt
  st <- IORef.readIORef pfmRef  
  let baseAbsSt = case Map.lookup faddr (functionAbstractStates st) of
        Just baseAbsSt' -> baseAbsSt'
        Nothing -> getInitAbsDomain pfm fnEntry
  -- FIXME: this ignores the stack
  absStRegs <- PRt.zipWithRegStatesM (baseAbsSt ^. DMAA.absRegState) (absSt0 ^. DMAA.absRegState) $ \r oldV newV -> return $ DMAA.meet oldV newV
  let absSt = (absSt0 & DMAA.absRegState .~ absStRegs)
  case eqAbsStates absSt0 absSt of
    True -> return ()
    False -> do
      let absSts' = Map.insert faddr absSt (functionAbstractStates st)
      -- this is a bit weird, should we actually discard the whole function cache, since
      -- we're discarding the discovery state?
      let cache' = Map.delete fnEntry (parsedFunctionCache st)
      let mkInit mem segOff = case Map.lookup segOff absSts' of
            _ -> MAI.mkInitialAbsState archInfo mem segOff
            Just newInit -> newInit
            Nothing -> MAI.mkInitialAbsState archInfo mem segOff
      let
        ainfo' = archInfo{ MAI.mkInitialAbsState = mkInit }
        -- reset the discovery state with this injected abstract state information
        dst' = MD.emptyDiscoveryState (MD.memory initSt) (MD.symbolNames initSt) ainfo'
        st' = st { parsedFunctionCache = mempty, functionAbstractStates = absSts', discoveryState = (discoveryState st) }      
      IORef.writeIORef pfmRef st'

-- | Return the 'MD.DiscoveryFunInfo' (raw macaw code discovery artifact)
-- corresponding to the function that contains the given basic block
--
-- This will return a cached parsed function if possible, or run the macaw
-- analysis on a new address and cache the result.
--
-- This function will return 'Nothing' if the address it is provided (via the
-- 'PB.ConcreteBlock') is not a mapped address in the binary. As long as the
-- address is valid, this function will at *least* return a single block with a
-- macaw translation error.
parsedFunctionContaining ::
  forall bin arch .
  (PBi.KnownBinary bin, MM.ArchConstraints arch) =>
  PB.ConcreteBlock arch bin ->
  ParsedFunctionMap arch bin ->
  IO (Maybe (Some (MD.DiscoveryFunInfo arch)))
parsedFunctionContaining blk (ParsedFunctionMap pfmRef mCFGDir pd _) = do
  let baddr = PA.addrToMemAddr (PB.concreteAddress blk)
  st <- IORef.readIORef pfmRef
  let ds1 = discoveryState st
  let mem = MD.memory ds1
  let ignoredAddresses :: Set.Set (MM.MemSegmentOff (MM.ArchAddrWidth arch))
      ignoredAddresses = Set.fromList $ case PC.knownRepr :: PBi.WhichBinaryRepr bin of
        PBi.OriginalRepr -> mapMaybe (segoffAddrsFor mem) (PC.ignoreOriginalFunctions pd)
        PBi.PatchedRepr -> mapMaybe (segoffAddrsFor mem) (PC.ignorePatchedFunctions pd)
  -- First, check if we have a cached set of blocks for this state
  case Map.lookup (PB.blockFunctionEntry blk) (parsedFunctionCache st) of
    Just sdfi -> return (Just sdfi)
    Nothing -> do
      -- Otherwise, run code discovery at this address
      case MM.resolveRegionOff mem (MM.addrBase baddr) (MM.addrOffset baddr) of
        Nothing -> return Nothing -- This could be an exception... but perhaps better as a proof failure node
        Just faddr -> do
          -- NOTE: We are using the strict atomic modify IORef here. The code is
          -- not attempting to force the modified state or returned function to
          -- normal form.
          --
          -- It isn't clear if this will be a problem in practice or not. We
          -- think that the worst case is that we end up with thunks in the
          -- IORef that might be evaluated multiple times if there is a lot of
          -- contention. If that becomes a problem, we may want to change this
          -- to an MVar where we fully evaluate each result before updating it.
          Some dfi <- IORef.atomicModifyIORef' pfmRef (atomicAnalysis ignoredAddresses faddr)
          let binRep :: PBi.WhichBinaryRepr bin
              binRep = PC.knownRepr
          saveCFG mCFGDir binRep dfi
          return (Just (Some dfi))
  where
    -- The entire analysis is bundled up in here so that we can issue an atomic
    -- update to the cache that preserves the discovery state. If we didn't do
    -- the analysis inside of the atomic update, we might lose updates to the
    -- discovery state. While that is not very important for our use case (we
    -- don't care about the preserved discovery state), it seems morally better
    -- to not lose those updates.
    --
    -- If the address is one that is to be ignored (as specified by the user),
    -- return an empty function (defined above). This empty function is handled
    -- normally by the rest of the verifier, and just produces a proof node with
    -- no interesting effects (thus skipping the ignored function and all of its
    -- callees). Note that by returning it here, the empty function is cached
    -- for the entry point address and reused if needed.
    atomicAnalysis ignoredAddressSet faddr st
      | Set.member faddr ignoredAddressSet = (st, Some (emptyFunction faddr))
      | otherwise =
        let rsn = MD.CallTarget faddr
        in case MD.analyzeFunction faddr rsn (discoveryState st) of
             (ds2, Some dfi) ->
               let entry = funInfoToFunEntry W4.knownRepr dfi
               in (st { parsedFunctionCache = Map.insert entry (Some dfi) (parsedFunctionCache st)
                      , discoveryState = ds2
                      }, Some dfi)

resolveFunctionEntry ::
  forall bin arch .
  (PBi.KnownBinary bin, MM.ArchConstraints arch) =>
  PB.FunctionEntry arch bin ->
  ParsedFunctionMap arch bin ->
  IO (Maybe (PB.FunctionEntry arch bin))
resolveFunctionEntry fe pfm = do
  let cb = PB.functionEntryToConcreteBlock fe
  parsedFunctionContaining cb pfm >>= \case
    Just (Some dfi) -> return $ Just $ funInfoToFunEntry PC.knownRepr dfi
    Nothing -> return Nothing

-- | Similar to 'parsedFunctionContaining', except that it constructs the
-- 'ParsedBlocks' structure used in most of the verifier.
parsedBlocksContaining ::
  forall bin arch .
  (PBi.KnownBinary bin, MM.ArchConstraints arch) =>
  PB.ConcreteBlock arch bin ->
  ParsedFunctionMap arch bin ->
  IO (Maybe (ParsedBlocks arch))
parsedBlocksContaining blk pfm =
  fmap (viewSome buildParsedBlocks) <$> parsedFunctionContaining blk pfm

findFunctionByName ::
  String ->
  ParsedFunctionMap arch bin ->
  IO (Maybe (PB.FunctionEntry arch bin))
findFunctionByName nm pfm = do
  st <- IORef.readIORef (parsedStateRef pfm)
  let fns = parsedFunctionCache st
  -- todo: match this better
  return $ F.find (\fe -> isJust (PB.functionSymbol fe) && nm == show (PB.functionSymbol fe)) (Map.keys fns)

isFunctionStart ::
  forall bin arch .
  (PBi.KnownBinary bin, MM.ArchConstraints arch) =>
  PB.ConcreteBlock arch bin ->
  ParsedFunctionMap arch bin ->
  IO Bool
isFunctionStart blk pfm = parsedFunctionContaining blk pfm >>= \case
  Just (Some dfi) -> do
    let baddr = PA.addrToMemAddr (PB.concreteAddress blk)
    return $ baddr == (MM.segoffAddr (MD.discoveredFunAddr dfi))
  Nothing -> return False

-- | Find the 'MD.ParsedBlock' corresponding to the entry of a function.
parsedBlockEntry ::
  forall bin arch .
  (PBi.KnownBinary bin, MM.ArchConstraints arch) =>
  PB.ConcreteBlock arch bin ->
  ParsedFunctionMap arch bin ->
  IO (Maybe (Some (MD.ParsedBlock arch)))
parsedBlockEntry blk pfm = parsedFunctionContaining blk pfm >>= \case
  Just (Some dfi) -> do
    st <- IORef.readIORef (parsedStateRef pfm)
    let ds1 = discoveryState st
    let mem = MD.memory ds1
    case MM.resolveRegionOff mem (MM.addrBase baddr) (MM.addrOffset baddr) of
      Nothing -> return Nothing
      Just faddr -> case Map.lookup faddr (dfi ^. MD.parsedBlocks) of
        Just pb -> return $ Just (Some pb)
        Nothing -> return Nothing
  Nothing -> return Nothing
  where
    baddr = PA.addrToMemAddr (PB.concreteAddress blk)
